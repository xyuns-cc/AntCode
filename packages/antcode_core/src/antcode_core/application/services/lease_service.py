"""Worker Lease 状态管理 — 替代心跳超时判活 (P3)

Lease 模型用 Redis 上的一组数据结构维护 Worker 的强一致存活状态：

- ``{{ns}}:lease:data:{worker_id}`` (Hash) —— namespace 使用 Redis hash tag，
  让 lease 主记录、撤销集和全局索引位于同一个 slot，可由单个 Lua 原子更新。字段:
    - ``lease_id``        — 16 字节 hex token，唯一标识本次 lease
    - ``expires_at_ms``   — 绝对过期时间（epoch ms）
    - ``granted_at_ms``   — 本次 lease 被发出 / 续租的时间
    - ``metrics_json``    — Worker 上报的 metrics（JSON dict，过渡期可选）

- ``{ns}:lease:expiring`` (ZSet) 全局单 key
    score = ``expires_at_ms``，member = ``worker_id``。
    ``sweep_expired`` 用 ``ZRANGEBYSCORE 0 now`` 取出所有过期 lease。

- ``{ns}:lease:active`` (Set) 全局单 key
    当前持有有效 lease 的 ``worker_id`` 集合，供 web_api 等只读消费方
    快速回答 "在线 worker 列表"。

设计要点：

- **集群兼容与原子性**：全部 lease key 使用 namespace hash tag，grant、revoke、
  sweep 可在同一个 Lua 中同步维护主记录与索引，不存在主记录成功但索引缺失。
- 未过期 lease 仅允许持有匹配 ``current_lease_id`` 的 Worker 续租；空 ID
  或不匹配 ID 会得到显式冲突，不能覆盖当前代际。无有效记录时才发新代际。
- ``sweep_expired`` 用 Lua CAS 清理过期记录；Master 端的
  ``LeaseSweeperLoop`` 每秒调一次即可。

Validates: Requirements (P3 设计文档 §Lease)
"""

from __future__ import annotations

import asyncio
import json
import secrets
from dataclasses import dataclass
from typing import Any

from loguru import logger

from antcode_core.infrastructure.redis.control_plane import redis_namespace

LEASE_RECORD_RETENTION_MS = 5_000


@dataclass(frozen=True)
class Lease:
    """单个 Worker 的 lease 快照（grant 后返回）。"""

    worker_id: str
    lease_id: str
    expires_at_ms: int
    granted_at_ms: int


class LeaseRevokedError(RuntimeError):
    """P1-15 fail-closed 信号：目标 worker 的 ``current_lease_id`` 出现在
    死信 revoked set 里,说明该 lease 已被 master/gateway 主动撤销,继续 grant
    会让被撤 worker 复活。上层应把此异常翻译成"revoked"响应,拒绝新租约。

    ``current_lease_id`` 为空时不会触发本异常——那是首发租的正常路径。
    """

    def __init__(self, worker_id: str, lease_id: str, message: str = ""):
        self.worker_id = worker_id
        self.lease_id = lease_id
        super().__init__(message or f"lease 已被撤销,拒绝再次 grant: worker_id={worker_id}")


class LeaseConflictError(RuntimeError):
    """目标 Worker 已有另一份未过期 lease，拒绝覆盖当前代际。"""

    def __init__(self, worker_id: str, current_lease_id: str, message: str = ""):
        self.worker_id = worker_id
        self.current_lease_id = current_lease_id
        super().__init__(message or f"worker 已有有效 lease,拒绝覆盖: worker_id={worker_id}")


@dataclass(frozen=True)
class LeasePolicy:
    """Lease 时长策略。

    Attributes:
        ttl_ms: 单次 lease 的存活时长（毫秒），过期未续即被剔除。
        renew_after_ms: 推荐 Worker 续租的间隔（毫秒），应明显小于 ttl_ms。
    """

    ttl_ms: int = 30_000
    renew_after_ms: int = 10_000


# P1-15 fail-closed: SISMEMBER revoked_key 拒绝 grant 被撤 lease_id 复活；outcome ∈ {new,renewed,revoked,conflict}。KEYS=[lease_key,revoked_key,expiring_zset,active_set]; ARGV=[worker_id,current_lease_id,new_lease_id,ttl_ms,record_retention_ms,metrics_json].
_GRANT_LUA = """
local lease_key   = KEYS[1]
local revoked_key = KEYS[2]
local expiring_key = KEYS[3]
local active_key = KEYS[4]

local worker_id        = ARGV[1]
local current_lease_id = ARGV[2]
local new_lease_id     = ARGV[3]
local ttl_ms           = tonumber(ARGV[4])
local record_retention_ms = tonumber(ARGV[5])
local metrics_json     = ARGV[6]

if ttl_ms == nil or ttl_ms < 1 then
    return redis.error_reply('lease ttl_ms must be positive')
end
if record_retention_ms == nil or record_retention_ms < 1 then
    return redis.error_reply('lease record_retention_ms must be positive')
end

-- Redis 时钟是 lease 时间线的唯一权威，避免应用节点时钟偏移造成越权续租。
local redis_time = redis.call('TIME')
local now_ms = tonumber(redis_time[1]) * 1000 + math.floor(tonumber(redis_time[2]) / 1000)
local expires_at_ms = now_ms + ttl_ms

-- P1-15 fail-closed: 目标 current_lease_id 若在死信集里就拒绝 grant
if current_lease_id ~= '' then
    if redis.call('SISMEMBER', revoked_key, current_lease_id) == 1 then
        return {'', '', '', 'revoked'}
    end
end

local stored_lease_id = redis.call('HGET', lease_key, 'lease_id')
local stored_expires  = tonumber(redis.call('HGET', lease_key, 'expires_at_ms') or "0")

-- P1-15 fail-closed（进阶）: 存储中的 lease_id 也可能被撤(edge case:
-- revoke 与 grant 并发时 revoke 只 DEL 了 lease_key 但 SADD 了 stored id)。
if stored_lease_id
   and redis.call('SISMEMBER', revoked_key, stored_lease_id) == 1 then
    return {'', '', '', 'revoked'}
end

local final_lease_id
local outcome

if stored_lease_id and stored_expires > now_ms then
    if current_lease_id ~= '' and stored_lease_id == current_lease_id then
        -- 续租：lease_id 不变，只刷新过期时间
        final_lease_id = stored_lease_id
        outcome = 'renewed'
    else
        -- 活跃代际只能由其持有者续租；冲突路径不得修改任何状态。
        return {'', '', '', 'conflict'}
    end
else
    -- 首次或上一代已过期：用新的 lease_id
    final_lease_id = new_lease_id
    outcome = 'new'
end

redis.call('HSET', lease_key,
    'lease_id', final_lease_id,
    'expires_at_ms', tostring(expires_at_ms),
    'granted_at_ms', tostring(now_ms),
    'worker_id', worker_id)

if metrics_json ~= '' then
    redis.call('HSET', lease_key, 'metrics_json', metrics_json)
end

-- 逻辑过期后短暂保留 Hash，供 sweeper 取得 doomed lease_id。
-- is_current 仅在 PTTL > record_retention_ms 时承认当前代际。
redis.call('PEXPIRE', lease_key, ttl_ms + record_retention_ms)
redis.call('ZADD', expiring_key, expires_at_ms, worker_id)
redis.call('SADD', active_key, worker_id)

return {final_lease_id, tostring(expires_at_ms), tostring(now_ms), outcome}
"""


# ---------------------------------------------------------------------------
# Lua 脚本：revoke lease_key + revoked_key（同 slot, P1-15）
#
# 非空 expected_lease_id 启用 compare-and-revoke：只有当前存储代际匹配时
# 才删除并写入 revoked_key，避免旧 Worker 撤销新代际。空 ID 是 Master
# 强制撤销路径。revoked_key 保留略大于 lease TTL 的窗口。
#
# KEYS: lease_key, revoked_key, expiring_zset, active_set
# ARGV:
#   1. worker_id
#   2. expected_lease_id
#   3. revoked_ttl_seconds
# 返回 1 (曾经有 lease) 或 0
# ---------------------------------------------------------------------------
_REVOKE_LUA = """
local lease_key   = KEYS[1]
local revoked_key = KEYS[2]
local expiring_key = KEYS[3]
local active_key = KEYS[4]

local worker_id = ARGV[1]
local expected_lease_id   = ARGV[2]
local revoked_ttl_seconds = tonumber(ARGV[3])
if revoked_ttl_seconds == nil or revoked_ttl_seconds < 1 then
    revoked_ttl_seconds = 1
end

local existed         = redis.call('EXISTS', lease_key)
local stored_lease_id = redis.call('HGET', lease_key, 'lease_id')

-- Worker 发起的撤销必须匹配其持有代际。检查与删除在同一脚本内完成，
-- 关闭 is_current + revoke 两步之间新 lease 被误删的 TOCTOU 窗口。
if expected_lease_id ~= '' and stored_lease_id ~= expected_lease_id then
    return 0
end

redis.call('DEL', lease_key)
redis.call('ZREM', expiring_key, worker_id)
redis.call('SREM', active_key, worker_id)

if stored_lease_id and stored_lease_id ~= '' then
    redis.call('SADD', revoked_key, stored_lease_id)
    redis.call('EXPIRE', revoked_key, revoked_ttl_seconds)
end

return existed
"""


# ---------------------------------------------------------------------------
# Lua 脚本：sweep CAS 删除（P1-15）
#
# ``sweep_expired`` 先 ZRANGEBYSCORE 拿到"看起来过期"的 worker_ids,再逐
# 个 DEL 时用本脚本 CAS 校验: 若 lease 已被续租(stored_expires_at_ms >
# now_ms),就跳过 DEL,只在快照 tick 已真正过期时才清 lease_key。
#
# KEYS: lease_key, expiring_zset, active_set
# ARGV: now_ms, worker_id
# 返回 {deleted, lease_id}；deleted 为 1 表示真的删了，为 0 表示已续租。
# ---------------------------------------------------------------------------
_SWEEP_DELETE_LUA = """
local lease_key = KEYS[1]
local expiring_key = KEYS[2]
local active_key = KEYS[3]
local now_ms    = tonumber(ARGV[1])
local worker_id = ARGV[2]

-- P1-03：一并读出即将被 DEL 的 lease_id，让上层在执行副作用前确认
-- 当前 Worker 尚未获得新 Lease，避免旧 eviction 误伤新代际。
local doomed_lease_id = redis.call('HGET', lease_key, 'lease_id')

local raw = redis.call('HGET', lease_key, 'expires_at_ms')
if raw == false or raw == nil then
    redis.call('ZREM', expiring_key, worker_id)
    redis.call('SREM', active_key, worker_id)
    return {1, doomed_lease_id or ''}
end
local stored_expires = tonumber(raw)
if stored_expires == nil then
    return {0, ''}
end
if stored_expires > now_ms then
    -- 已被续租,快照过期时刻已作废,不能 DEL
    return {0, ''}
end
redis.call('DEL', lease_key)
redis.call('ZREM', expiring_key, worker_id)
redis.call('SREM', active_key, worker_id)
return {1, doomed_lease_id or ''}
"""


class LeaseStore:
    """基于 Redis 的 Worker Lease 状态机。

    所有 Key 都纳入 ``{namespace}:lease:*`` 前缀，与 ``RedisKeys`` /
    ``control_plane`` 现有命名空间约定一致。
    """

    LEASE_KEY_TEMPLATE = "{{{ns}}}:lease:data:{worker_id}"
    REVOKED_SET_TEMPLATE = "{{{ns}}}:lease:revoked:{worker_id}"
    EXPIRING_ZSET_SUFFIX = "lease:expiring"
    ACTIVE_SET_SUFFIX = "lease:active"
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
            policy: lease 时长策略；未提供则用默认 30s TTL / 10s 续租。
        """
        self._redis = redis_client
        self._namespace = redis_namespace(namespace)
        self._policy = policy or LeasePolicy()
        self._grant_script_sha: str | None = None
        self._revoke_script_sha: str | None = None
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
            if self._sweep_delete_script_sha is None:
                self._sweep_delete_script_sha = await self._redis.script_load(_SWEEP_DELETE_LUA)
            self._scripts_loaded.set()

    async def _evalsha_grant(self, keys: list[str], args: list[str]) -> list[Any]:
        await self._ensure_scripts_loaded()
        try:
            return await self._redis.evalsha(self._grant_script_sha, len(keys), *keys, *args)
        except Exception as exc:
            # NOSCRIPT 或缓存失效时回退到 EVAL，并触发下一次 _ensure_scripts_loaded
            # 重新缓存 SHA(Event 清零让 double-check 路径再走一遍 SCRIPT LOAD)。
            if "NOSCRIPT" in str(exc):
                logger.warning("Lease grant 脚本未在 Redis 缓存中，回退 EVAL")
                self._grant_script_sha = None
                self._scripts_loaded.clear()
                return await self._redis.eval(_GRANT_LUA, len(keys), *keys, *args)
            raise

    async def _evalsha_revoke(self, keys: list[str], args: list[str]) -> Any:
        await self._ensure_scripts_loaded()
        try:
            return await self._redis.evalsha(self._revoke_script_sha, len(keys), *keys, *args)
        except Exception as exc:
            if "NOSCRIPT" in str(exc):
                logger.warning("Lease revoke 脚本未在 Redis 缓存中，回退 EVAL")
                self._revoke_script_sha = None
                self._scripts_loaded.clear()
                return await self._redis.eval(_REVOKE_LUA, len(keys), *keys, *args)
            raise

    async def _evalsha_sweep_delete(self, keys: list[str], args: list[str]) -> Any:
        """P1-15 sweep CAS: 只有在 stored_expires_at_ms ≤ now_ms 时才 DEL lease_key。"""
        await self._ensure_scripts_loaded()
        try:
            return await self._redis.evalsha(self._sweep_delete_script_sha, len(keys), *keys, *args)
        except Exception as exc:
            if "NOSCRIPT" in str(exc):
                logger.warning("Lease sweep 脚本未在 Redis 缓存中，回退 EVAL")
                self._sweep_delete_script_sha = None
                self._scripts_loaded.clear()
                return await self._redis.eval(_SWEEP_DELETE_LUA, len(keys), *keys, *args)
            raise

    # ------------------------------------------------------------------ API

    async def grant(
        self,
        worker_id: str,
        current_lease_id: str = "",
        metrics: dict[str, Any] | None = None,
    ) -> Lease:
        """首次发租或续租。

        Args:
            worker_id: Worker 标识。
            current_lease_id: Worker 端持有的 lease_id，空表示首次发租。
            metrics: 可选的指标快照，序列化为 JSON 落入 Hash（运维 dashboard 兼容）。

        Returns:
            包含最终 ``lease_id`` 与过期时间的 ``Lease`` 对象。

        Raises:
            ValueError: ``worker_id`` 为空。
            LeaseConflictError: 已有未过期 lease，且调用方未持有匹配代际。
            LeaseRevokedError: 调用方持有的 lease 已被主动撤销。
        """
        if not worker_id:
            raise ValueError("worker_id 不能为空")

        new_lease_id = secrets.token_hex(16)

        metrics_json = ""
        if metrics:
            try:
                metrics_json = json.dumps(metrics, ensure_ascii=False, default=str)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(f"Lease grant: metrics 序列化失败，忽略: {exc}")
                metrics_json = ""

        keys = [
            self._lease_key(worker_id),
            self._revoked_key(worker_id),
            self._expiring_zset(),
            self._active_set(),
        ]
        args = [
            worker_id,
            current_lease_id or "",
            new_lease_id,
            str(int(self._policy.ttl_ms)),
            str(LEASE_RECORD_RETENTION_MS),
            metrics_json,
        ]
        result = await self._evalsha_grant(keys, args)

        # Lua 返回 [lease_id, expires_at_ms, granted_at_ms, outcome]。
        # rejected outcome 的前三项均为空串。
        outcome = _to_str(result[3]) if len(result) > 3 else ""
        _ensure_grant_accepted(outcome, worker_id, current_lease_id or "")

        final_lease_id = _to_str(result[0])
        final_expires = int(_to_str(result[1]))
        final_granted = int(_to_str(result[2]))

        if outcome == "new":
            logger.debug(f"Lease 新发: worker_id={worker_id}, expires_at_ms={final_expires}")
        else:
            logger.debug(f"Lease 续租: worker_id={worker_id}, expires_at_ms={final_expires}")

        return Lease(
            worker_id=worker_id,
            lease_id=final_lease_id,
            expires_at_ms=final_expires,
            granted_at_ms=final_granted,
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
        的 hash tag 里都锁到同 slot ({{worker_id}}), Redis Cluster 上安全。
        """
        if not worker_id or not lease_id:
            return False
        lease_key = self._lease_key(worker_id)
        revoked_key = self._revoked_key(worker_id)
        pipeline = self._redis.pipeline(transaction=True)
        pipeline.sismember(revoked_key, lease_id)
        pipeline.exists(lease_key)
        pipeline.hget(lease_key, "lease_id")
        pipeline.pttl(lease_key)
        is_revoked, exists, stored_lease_id, pttl_ms = await pipeline.execute()
        if bool(is_revoked):
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
            deleted_flag, doomed_lease_id = _parse_sweep_delete_result(result)
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
# helpers
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
    if outcome == "revoked":
        raise LeaseRevokedError(worker_id=worker_id, lease_id=current_lease_id)
    if outcome == "conflict":
        raise LeaseConflictError(worker_id=worker_id, current_lease_id=current_lease_id)
    raise RuntimeError(f"Lease grant Lua 返回未知 outcome: {outcome!r}")


def _parse_sweep_delete_result(result: Any) -> tuple[int, str]:
    if not isinstance(result, (list, tuple)) or len(result) != 2:
        raise RuntimeError(f"Lease sweep Lua 返回结构非法: {result!r}")
    deleted_flag = int(result[0] or 0)
    if deleted_flag not in (0, 1):
        raise RuntimeError(f"Lease sweep Lua deleted 标志非法: {deleted_flag!r}")
    return deleted_flag, _to_str(result[1])


__all__ = [
    "Lease",
    "LeaseConflictError",
    "LeasePolicy",
    "LeaseRevokedError",
    "LeaseStore",
]
