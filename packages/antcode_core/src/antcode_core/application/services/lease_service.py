"""Worker Lease 状态管理 — 替代心跳超时判活 (P3)

Lease 模型用 Redis 上的一组数据结构维护 Worker 的强一致存活状态：
- ``{{ns}}:lease:data:{worker_id}`` (Hash) —— namespace 使用 Redis hash tag，
  让 lease 主记录、撤销集和全局索引位于同一个 slot，可由单个 Lua 原子更新。字段:
    - ``lease_id``        — 16 字节 hex token，唯一标识本次 lease
    - ``expires_at_ms``   — 绝对过期时间（epoch ms）
    - ``granted_at_ms``   — 本次 lease 被发出 / 续租的时间
    - ``metrics_json`` / ``capabilities_json`` — Worker 指标与能力 JSON
- ``{ns}:lease:expiring`` / ``{ns}:lease:active`` 维护过期索引和在线集合。

设计要点：

- **集群兼容与原子性**：全部 lease key 使用 namespace hash tag，grant、revoke、
  sweep 可在同一个 Lua 中同步维护主记录与索引，不存在主记录成功但索引缺失。
- 未过期 lease 仅允许持有匹配 ``current_lease_id`` 的 Worker 续租；空 ID
  或不匹配 ID 会得到显式冲突，不能覆盖当前代际。无有效记录时才发新代际。
- ``sweep_expired`` 用 Lua CAS 清理过期记录；Master 端的
  ``LeaseSweeperLoop`` 每秒调一次即可。
"""

from __future__ import annotations

import asyncio
import secrets
from typing import Any

from loguru import logger
from redis.exceptions import NoScriptError

from antcode_core.application.services.lease_models import (
    Lease,
    LeaseConflictError,
    LeaseIneligibleError,
    LeasePolicy,
    LeaseRevokedError,
    wire_lease_policy,
)
from antcode_core.application.services.lease_scripts import DISABLE_WORKER_LUA as _DISABLE_WORKER_LUA
from antcode_core.application.services.lease_scripts import ENABLE_WORKER_LUA as _ENABLE_WORKER_LUA
from antcode_core.application.services.lease_scripts import GRANT_LUA as _GRANT_LUA
from antcode_core.application.services.lease_scripts import REVOKE_LUA as _REVOKE_LUA
from antcode_core.application.services.lease_scripts import SWEEP_DELETE_LUA as _SWEEP_DELETE_LUA
from antcode_core.application.services.lease_scripts import parse_sweep_delete_result
from antcode_core.application.services.lease_serialization import serialize_lease_value
from antcode_core.infrastructure.redis.control_plane import redis_namespace

LEASE_RECORD_RETENTION_MS = 5_000


# Redis programs live in lease_scripts.py; aliases above preserve the public test contract.


class LeaseStore:
    """基于 Redis 的 Worker Lease 状态机。

    所有 Key 都纳入 ``{namespace}:lease:*`` 前缀，与 ``RedisKeys`` /
    ``control_plane`` 现有命名空间约定一致。
    """

    LEASE_KEY_TEMPLATE = "{{{ns}}}:lease:data:{worker_id}"
    REVOKED_SET_TEMPLATE = "{{{ns}}}:lease:revoked:{worker_id}"
    EXPIRING_ZSET_SUFFIX = "lease:expiring"
    ACTIVE_SET_SUFFIX = "lease:active"
    # P1-GW-01 (round6): 全局单调 sequence 计数器,与 lease_key 同 hash slot。
    # grant INCR 拿到严格单调值,作 lease_gen tie-breaker;同毫秒下 L1/L2
    # 也拿不同 seq。所有 worker 共用同一 seq_key(Redis Cluster 下 hash tag
    # 保证同 slot,不跨节点)。
    SEQ_KEY_TEMPLATE = "{{{ns}}}:lease:sequence"
    LIFECYCLE_KEY_TEMPLATE = "{{{ns}}}:lease:lifecycle:{worker_id}"
    # P1-15 死信保留额外冗余(秒),防止旧 worker 在死信 TTL 边界赢下竞赛
    REVOKED_TTL_MARGIN_SECONDS = 30

    def __init__(
        self,
        redis_client: Any,
        namespace: str | None = None,
        policy: LeasePolicy | None = None,
    ):
        """构造 LeaseStore。

        Args:
            redis_client: 已连接的 ``redis.asyncio.Redis`` 实例。
            namespace: Key 命名空间前缀；缺省经 ``redis_namespace()`` 解析
                ``settings.REDIS_NAMESPACE``（默认 ``antcode``）。此前默认
                硬编码 ``antcode``，非默认 REDIS_NAMESPACE 部署时 sweeper /
                metrics 会扫到空 keyspace。
            policy: lease 时长策略；未提供则取跨服务共享的 ``wire_lease_policy()``。
        """
        self._redis = redis_client
        self._namespace = redis_namespace(namespace)
        self._policy = policy or wire_lease_policy()
        self._grant_script_sha: str | None = None
        self._revoke_script_sha: str | None = None
        self._disable_worker_script_sha: str | None = None
        self._enable_worker_script_sha: str | None = None
        self._sweep_delete_script_sha: str | None = None
        # _ensure_scripts_loaded 用 Event 做快速路径(无锁开销),用 Lock 做
        # 慢路径串行化:第一次 SCRIPT LOAD 期间多个 grant/revoke 并发到达时,
        # 只让一个协程真正发 SCRIPT LOAD,其它人在锁外面等 Event。
        self._scripts_loaded = asyncio.Event()
        self._scripts_lock = asyncio.Lock()

    # ------------------------------------------------------------------ Keys

    def _lease_key(self, worker_id: str) -> str:
        return self.LEASE_KEY_TEMPLATE.format(ns=self._namespace, worker_id=worker_id)

    def lease_key(self, worker_id: str) -> str:
        """Return the authoritative Redis key for one worker's lease."""
        return self._lease_key(worker_id)

    def lifecycle_key(self, worker_id: str) -> str:
        """Return the administrative lifecycle fence key for one Worker."""
        return self.LIFECYCLE_KEY_TEMPLATE.format(ns=self._namespace, worker_id=worker_id)

    def _revoked_key(self, worker_id: str) -> str:
        # P1-15: 与 lease_key 同 slot 的 revoked lease_id 死信集
        return self.REVOKED_SET_TEMPLATE.format(ns=self._namespace, worker_id=worker_id)

    def _revoked_ttl_seconds(self) -> int:
        """revoked set 存活时长: lease TTL + margin(≥30s)。"""
        ttl_ms = int(self._policy.ttl_ms) + self.REVOKED_TTL_MARGIN_SECONDS * 1000
        seconds = ttl_ms // 1000
        if seconds < self.REVOKED_TTL_MARGIN_SECONDS:
            seconds = self.REVOKED_TTL_MARGIN_SECONDS
        return int(seconds)

    def _expiring_zset(self) -> str:
        return f"{{{self._namespace}}}:{self.EXPIRING_ZSET_SUFFIX}"

    def _active_set(self) -> str:
        return f"{{{self._namespace}}}:{self.ACTIVE_SET_SUFFIX}"

    def _seq_key(self) -> str:
        # P1-GW-01 (round6): grant 全局单调 sequence 键
        return self.SEQ_KEY_TEMPLATE.format(ns=self._namespace)

    @property
    def policy(self) -> LeasePolicy:
        return self._policy

    @property
    def namespace(self) -> str:
        return self._namespace

    # ---------------------------------------------------------------- Script

    async def _ensure_scripts_loaded(self) -> None:
        """首次调用时 SCRIPT LOAD，把 SHA 缓存下来。

        采用 double-checked locking:Event 给出 lock-free 的快速路径,Lock 把
        实际的 SCRIPT LOAD 串行化,避免并发 grant/revoke 第一次进入时同时发
        SCRIPT LOAD 重复加载、或在两个属性赋值中间被另一个协程读到半初始化
        状态。
        """
        if self._scripts_loaded.is_set():
            return
        async with self._scripts_lock:
            # 拿到锁后再确认一次:可能在 await lock 期间别人已经完成加载
            if self._scripts_loaded.is_set():
                return
            if self._grant_script_sha is None:
                self._grant_script_sha = await self._redis.script_load(_GRANT_LUA)
            if self._revoke_script_sha is None:
                self._revoke_script_sha = await self._redis.script_load(_REVOKE_LUA)
            if self._disable_worker_script_sha is None:
                self._disable_worker_script_sha = await self._redis.script_load(_DISABLE_WORKER_LUA)
            if self._enable_worker_script_sha is None:
                self._enable_worker_script_sha = await self._redis.script_load(_ENABLE_WORKER_LUA)
            if self._sweep_delete_script_sha is None:
                self._sweep_delete_script_sha = await self._redis.script_load(_SWEEP_DELETE_LUA)
            self._scripts_loaded.set()

    async def _evalsha_grant(self, keys: list[str], args: list[str]) -> list[Any]:
        await self._ensure_scripts_loaded()
        try:
            return await self._redis.evalsha(self._grant_script_sha, len(keys), *keys, *args)
        except NoScriptError:
            # Redis 重启 / SCRIPT FLUSH / 副本切换后 SHA 必然失效，按协议要求重新加载。
            # 必须捕获类型而不是匹配 str(exc)："NOSCRIPT" 只出现在服务端错误码里，
            # redis-py 映射成 NoScriptError 时已剥掉前缀，str() 是
            # "No matching script. Please use EVAL."——按子串判断永远不成立，
            # Redis 一重启，全集群 Lease 签发就永久失败到进程重启为止（真机实测）。
            logger.warning("Lease grant 脚本未在 Redis 缓存中，回退 EVAL")
            self._grant_script_sha = None
            self._scripts_loaded.clear()
            return await self._redis.eval(_GRANT_LUA, len(keys), *keys, *args)

    async def _evalsha_revoke(self, keys: list[str], args: list[str]) -> Any:
        await self._ensure_scripts_loaded()
        try:
            return await self._redis.evalsha(self._revoke_script_sha, len(keys), *keys, *args)
        except NoScriptError:
            logger.warning("Lease revoke 脚本未在 Redis 缓存中，回退 EVAL")
            self._revoke_script_sha = None
            self._scripts_loaded.clear()
            return await self._redis.eval(_REVOKE_LUA, len(keys), *keys, *args)

    async def _evalsha_disable_worker(self, keys: list[str], args: list[str]) -> Any:
        await self._ensure_scripts_loaded()
        try:
            return await self._redis.evalsha(self._disable_worker_script_sha, len(keys), *keys, *args)
        except NoScriptError:
            self._disable_worker_script_sha = None
            self._scripts_loaded.clear()
            return await self._redis.eval(_DISABLE_WORKER_LUA, len(keys), *keys, *args)

    async def _evalsha_enable_worker(self, keys: list[str], args: list[str]) -> Any:
        await self._ensure_scripts_loaded()
        try:
            return await self._redis.evalsha(self._enable_worker_script_sha, len(keys), *keys, *args)
        except NoScriptError:
            self._enable_worker_script_sha = None
            self._scripts_loaded.clear()
            return await self._redis.eval(_ENABLE_WORKER_LUA, len(keys), *keys, *args)

    async def _evalsha_sweep_delete(self, keys: list[str], args: list[str]) -> Any:
        """P1-15 sweep CAS: 只有在 stored_expires_at_ms ≤ now_ms 时才 DEL lease_key。"""
        await self._ensure_scripts_loaded()
        try:
            return await self._redis.evalsha(self._sweep_delete_script_sha, len(keys), *keys, *args)
        except NoScriptError:
            logger.warning("Lease sweep 脚本未在 Redis 缓存中，回退 EVAL")
            self._sweep_delete_script_sha = None
            self._scripts_loaded.clear()
            return await self._redis.eval(_SWEEP_DELETE_LUA, len(keys), *keys, *args)

    # ------------------------------------------------------------------ API

    async def grant(
        self,
        worker_id: str,
        current_lease_id: str = "",
        metrics: dict[str, Any] | None = None,
        *,
        capabilities: dict[str, Any] | None = None,
    ) -> Lease:
        """首次发租或续租。

        Args:
            worker_id: Worker 标识。
            current_lease_id: Worker 端持有的 lease_id，空表示首次发租。
            metrics: 可选的指标快照。
            capabilities: 已校验能力，和 Lease 在同一 Lua 中原子持久化。

        Returns:
            包含最终 ``lease_id`` 与过期时间的 ``Lease`` 对象。

        Raises:
            ValueError: ``worker_id`` 为空。
            LeaseIneligibleError: Worker 被权威生命周期栅栏禁用。
            LeaseConflictError: 已有未过期 lease，且调用方未持有匹配代际。
            LeaseRevokedError: 调用方持有的 lease 已被主动撤销。
        """
        if not worker_id:
            raise ValueError("worker_id 不能为空")

        new_lease_id = secrets.token_hex(16)

        metrics_json = serialize_lease_value(metrics)
        capabilities_json = serialize_lease_value(capabilities, preserve_empty=True)

        keys = [
            self._lease_key(worker_id),
            self._revoked_key(worker_id),
            self._expiring_zset(),
            self._active_set(),
            self._seq_key(),  # P1-GW-01 (round6): grant seq 计数器
            self.lifecycle_key(worker_id),
        ]
        args = [
            worker_id,
            current_lease_id or "",
            new_lease_id,
            str(int(self._policy.ttl_ms)),
            str(LEASE_RECORD_RETENTION_MS),
            metrics_json,
            capabilities_json,
        ]
        result = await self._evalsha_grant(keys, args)

        # Lua 返回 [lease_id, expires_at_ms, granted_at_ms, outcome, sequence]。
        # rejected outcome 的前三项均为空串;sequence 位是 round6 新增的严格
        # 单调 tie-breaker,老 Lua/存量 lease 无该位时 fallback 到 0。
        outcome = _to_str(result[3]) if len(result) > 3 else ""
        _ensure_grant_accepted(outcome, worker_id, current_lease_id or "")

        final_lease_id = _to_str(result[0])
        final_expires = int(_to_str(result[1]))
        final_granted = int(_to_str(result[2]))
        # P1-GW-01 (round6): sequence 是新加的第 5 项;老脚本无该项时 fallback 0
        final_sequence = int(_to_str(result[4])) if len(result) > 4 else 0

        if outcome == "new":
            logger.debug(f"Lease 新发: worker_id={worker_id}, expires_at_ms={final_expires}, seq={final_sequence}")
        else:
            logger.debug(f"Lease 续租: worker_id={worker_id}, expires_at_ms={final_expires}, seq={final_sequence}")

        return Lease(
            worker_id=worker_id,
            lease_id=final_lease_id,
            expires_at_ms=final_expires,
            granted_at_ms=final_granted,
            sequence=final_sequence,
        )

    async def revoke(
        self,
        worker_id: str,
        reason: str = "",
        lease_id: str = "",
    ) -> bool:
        """Master / Gateway 主动撤销 Worker 的 lease。

        P1-15: 除了 DEL lease_key,还把被撤 lease_id 写入死信集
        ``{ns}:{{worker_id}}:lease:revoked``,窗口为 ``lease TTL + 30s
        margin``。非空 ``lease_id`` 是 expected generation，只有与 Redis
        当前记录匹配才撤销；空值保留给 Master 强制撤销。

        Args:
            worker_id: Worker 标识。
            reason: 仅用于日志，便于运维审计。
            lease_id: 可选 expected generation；非空且不匹配时不做任何写入。

        Returns:
            True 表示匹配的 lease 被撤销；False 表示无 lease 或代际不匹配。
        """
        if not worker_id:
            return False

        keys = [
            self._lease_key(worker_id),
            self._revoked_key(worker_id),
            self._expiring_zset(),
            self._active_set(),
        ]
        args = [
            worker_id,
            lease_id or "",
            str(self._revoked_ttl_seconds()),
        ]
        result = await self._evalsha_revoke(keys, args)
        revoked = int(result or 0) > 0
        if revoked:
            logger.info(f"Lease 已撤销: worker_id={worker_id}, reason={reason or 'unspecified'}")
        return revoked

    async def disable_worker(self, worker_id: str, *, reason: str, heartbeat_key: str) -> bool:
        """Atomically install a lifecycle fence and revoke all active state."""
        if not worker_id or not heartbeat_key:
            raise ValueError("worker_id and heartbeat_key are required")
        keys = [
            self._lease_key(worker_id),
            self._revoked_key(worker_id),
            self._expiring_zset(),
            self._active_set(),
            self.lifecycle_key(worker_id),
            heartbeat_key,
        ]
        args = [worker_id, reason, str(self._revoked_ttl_seconds())]
        disabled = int(await self._evalsha_disable_worker(keys, args) or 0) > 0
        logger.info("Worker lease lifecycle disabled: worker_id={} reason={}", worker_id, reason)
        return disabled

    async def enable_worker(
        self,
        worker_id: str,
        *,
        expected_reasons: tuple[str, ...],
        allow_mismatch: bool = False,
    ) -> bool:
        """Atomically clear a lifecycle fence only for an allowed reason."""
        if not worker_id:
            raise ValueError("worker_id is required")
        if not expected_reasons:
            raise ValueError("expected_reasons are required")
        result = int(
            await self._evalsha_enable_worker(
                [self.lifecycle_key(worker_id)],
                list(expected_reasons),
            )
            or 0
        )
        if result < 0 and not allow_mismatch:
            raise LeaseIneligibleError(worker_id)
        return result > 0

    async def get(self, worker_id: str, include_expired: bool = True) -> Lease | None:
        """读取当前 lease 快照（不刷新过期时间）。

        Args:
            worker_id: Worker 标识。
            include_expired: 为 False 时对照 Redis TIME 校验 ``expires_at_ms``，
                逻辑已过期（Hash 仍在 retention 窗口内）的记录返回 None。
                eviction superseded 判定等"是否存在**有效**新代际"的场景必须
                用 False，否则会把过期残留记录误判为有效 lease。
        """
        if not worker_id:
            return None
        data = await self._redis.hgetall(self._lease_key(worker_id))
        if not data:
            return None
        decoded = {_to_str(k): _to_str(v) for k, v in data.items()}
        lease_id = decoded.get("lease_id", "")
        if not lease_id:
            return None
        try:
            expires_at_ms = int(decoded.get("expires_at_ms", "0"))
            granted_at_ms = int(decoded.get("granted_at_ms", "0"))
            # P1-GW-01 (round6): sequence 是 round6 新加字段;存量 lease 无此字段
            # 时 fallback 0(向后兼容,只是失去同毫秒 tie-breaker 效果)
            sequence = int(decoded.get("sequence", "0"))
        except ValueError:
            return None
        if not include_expired:
            now_ms = await self._redis_time_ms()
            if expires_at_ms <= now_ms:
                return None
        return Lease(
            worker_id=worker_id,
            lease_id=lease_id,
            expires_at_ms=expires_at_ms,
            granted_at_ms=granted_at_ms,
            sequence=sequence,
        )

    async def is_active(self, worker_id: str) -> bool:
        """快速判活：worker_id 是否仍在 ``lease:active`` 集合中。"""
        if not worker_id:
            return False
        result = await self._redis.sismember(self._active_set(), worker_id)
        return bool(result)

    async def is_current(self, worker_id: str, lease_id: str) -> bool:
        """Return whether Redis still holds the exact generation with a live TTL.

        P1-DR-02: 增加 revoked set 检查 (SISMEMBER lease:revoked:{worker_id})。
        原实现只验 lease_key.HGET(lease_id) 与 PTTL,revoke 后 Master 在其他
        路径(grant_lease Lua/_REVOKE_LEASE_LUA)才检查 revoked set。撤销的
        Worker 若在旧代际 grant 还残留 (lease_key 尚未被 revoke Lua 清除,
        或撤销发起方无权改 key), is_current 会返回 True,让旧代际的结算/
        ownership renew 继续放行。
        现在:先 SISMEMBER revoked_key(lease_id) 命中即 fail-closed 返回 False,
        再走 lease_key 检查;整体仍走 pipeline (transaction=True) 保持原子。
        SISMEMBER 与 lease_key HGET 在 REVOKED_SET_TEMPLATE / LEASE_KEY_TEMPLATE
        的 hash tag 里都锁到同 slot ({{namespace}}), Redis Cluster 上安全。
        """
        if not worker_id or not lease_id:
            return False
        lease_key = self._lease_key(worker_id)
        revoked_key = self._revoked_key(worker_id)
        lifecycle_key = self.lifecycle_key(worker_id)
        pipeline = self._redis.pipeline(transaction=True)
        pipeline.sismember(revoked_key, lease_id)
        pipeline.exists(lifecycle_key)
        pipeline.exists(lease_key)
        pipeline.hget(lease_key, "lease_id")
        pipeline.pttl(lease_key)
        is_revoked, is_disabled, exists, stored_lease_id, pttl_ms = await pipeline.execute()
        if bool(is_revoked) or bool(is_disabled):
            return False
        return bool(exists) and _to_str(stored_lease_id) == lease_id and int(pttl_ms) > LEASE_RECORD_RETENTION_MS

    async def list_active(self) -> list[str]:
        """返回当前所有持有 lease 的 worker_id。"""
        members = await self._redis.smembers(self._active_set())
        return sorted(_to_str(m) for m in members)

    async def sweep_expired(
        self,
        now_ms: int | None = None,
        batch: int = 100,
    ) -> list[tuple[str, str]]:
        """扫描过期 lease，剔除离线 Worker。

        P1-15 CAS: ZRANGEBYSCORE 拿到的是"某个瞬间过期"的 worker_ids 快照;
        在 sweep 与 DEL 之间, worker 可能已经续租(expires_at_ms 被推后)。
        本方法用 ``_SWEEP_DELETE_LUA`` 在 DEL 前重新 HGET expires_at_ms,只
        有真过期(stored ≤ now_ms)才 DEL,避免误删刚续租的 lease。ZSet/Set
        的 index 清理只对真正被 DEL 的 worker 执行。

        P1-03（代际信息）：返回 ``(worker_id, evicted_lease_id)`` 元组，让
        上层 eviction 回调在写副作用前重新读取当前 Lease，避免旧 lease
        被 sweep 后 Worker 已换新 lease 时误把新代际判为失联。

        Args:
            now_ms: 截止时间（毫秒）；默认取当前时间。
            batch: 每轮 ZRANGEBYSCORE 的上限，避免单次清理过多 worker 阻塞 loop。

        Returns:
            本次被剔除的 ``(worker_id, evicted_lease_id)`` 列表，按 expires_at 升序。
            ``evicted_lease_id`` 来自即将删除的 Lease Hash；缺失时由回调显式
            报错，禁止在无法确认代际时执行 Worker/TaskRun 副作用。
        """
        if now_ms is None:
            now_ms = await self._redis_time_ms()

        expiring_key = self._expiring_zset()

        # ZRANGEBYSCORE 取过期成员
        raw = await self._redis.zrangebyscore(
            expiring_key,
            min=0,
            max=now_ms,
            start=0,
            num=max(1, batch),
        )
        if not raw:
            return []

        worker_ids = [_to_str(w) for w in raw if _to_str(w)]
        if not worker_ids:
            return []

        # 逐个 CAS-delete；Lua 同时维护主记录、过期索引与 active set。
        evicted: list[tuple[str, str]] = []
        for worker_id in worker_ids:
            try:
                result = await self._evalsha_sweep_delete(
                    [
                        self._lease_key(worker_id),
                        expiring_key,
                        self._active_set(),
                    ],
                    [str(now_ms), worker_id],
                )
            except Exception as exc:
                logger.warning(f"Lease sweep CAS 校验失败,跳过本 worker: worker_id={worker_id}, err={exc}")
                continue
            deleted_flag, doomed_lease_id = parse_sweep_delete_result(result)
            if deleted_flag > 0:
                evicted.append((worker_id, doomed_lease_id))

        if evicted:
            logger.info(f"Lease sweep 剔除 {len(evicted)} 个过期 worker: {evicted}")
        return evicted

    async def _redis_time_ms(self) -> int:
        """Read the authoritative lease timeline from Redis TIME."""
        raw = await self._redis.time()
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            raise RuntimeError(f"Redis TIME 返回结构非法: {raw!r}")
        seconds, microseconds = (int(value) for value in raw)
        return seconds * 1000 + microseconds // 1000


# ----------------------------------------------------------------------------
def _to_str(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    if value is None:
        return ""
    return str(value)


def _ensure_grant_accepted(outcome: str, worker_id: str, current_lease_id: str) -> None:
    if outcome in {"new", "renewed"}:
        return
    logger.warning(f"Lease grant 被拒绝({outcome or 'invalid'}): worker_id={worker_id}")
    if outcome in {"revoked", "capabilities_changed"}:
        message = "Lease 能力快照已变更，必须使用新代际" if outcome == "capabilities_changed" else ""
        raise LeaseRevokedError(worker_id=worker_id, lease_id=current_lease_id, message=message)
    if outcome == "ineligible":
        raise LeaseIneligibleError(worker_id)
    if outcome == "conflict":
        raise LeaseConflictError(worker_id=worker_id, current_lease_id=current_lease_id)
    raise RuntimeError(f"Lease grant Lua 返回未知 outcome: {outcome!r}")


__all__ = [
    "Lease",
    "LeaseConflictError",
    "LeaseIneligibleError",
    "LeasePolicy",
    "LeaseRevokedError",
    "LeaseStore",
    "wire_lease_policy",
]
