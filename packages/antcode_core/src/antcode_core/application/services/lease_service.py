"""Worker Lease 状态管理 — 替代心跳超时判活 (P3)

Lease 模型用 Redis 上的一组数据结构维护 Worker 的强一致存活状态：

- ``{ns}:{{worker_id}}:lease:data`` (Hash) —— T6-T1c: worker_id 用 Redis hash
  tag 语法 ``{...}`` 包起来，让 Redis Cluster 把同一个 worker 的所有 lease
  数据落在同一个 slot。字段:
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

- **T6-T1c 集群兼容**：Lua 脚本只操作 lease_key 单 key（同 slot 安全），
  ZADD/SADD 全局索引移到 pipeline 里，pipeline(transaction=False) 在集群下
  会按 key 路由到对应节点。lease_key 的 EXPIRE 是兜底 —— 即使 index 更新
  丢失，Hash 也会自然过期，避免脏数据长期滞留。
- ``current_lease_id`` 不匹配（worker 重启 / lease 已被撤销）一律视为
  "首次发租"，生成新 lease_id 并刷新所有结构，让旧 token 自然失效。
- ``sweep_expired`` 用 pipeline 批量清理；Master 端的
  ``LeaseSweeperLoop`` 每秒调一次即可。

Validates: Requirements (P3 设计文档 §Lease)
"""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any

from loguru import logger


@dataclass(frozen=True)
class Lease:
    """单个 Worker 的 lease 快照（grant 后返回）。"""

    worker_id: str
    lease_id: str
    expires_at_ms: int
    granted_at_ms: int


@dataclass(frozen=True)
class LeasePolicy:
    """Lease 时长策略。

    Attributes:
        ttl_ms: 单次 lease 的存活时长（毫秒），过期未续即被剔除。
        renew_after_ms: 推荐 Worker 续租的间隔（毫秒），应明显小于 ttl_ms。
    """

    ttl_ms: int = 30_000
    renew_after_ms: int = 10_000


# ---------------------------------------------------------------------------
# Lua 脚本：grant lease_key 单 key 操作
#
# T6-T1c: Redis Cluster 下 EVAL 只能碰同 slot 的 key。原实现一次改
# lease_key + expiring_zset + active_set 三个跨 slot key 会 CROSSSLOT 报错。
# 拆成"Lua 只管 lease_key，index 走 pipeline"两段——lease_key 有 EXPIRE
# 兜底，index 漏更新最坏情况就是 sweep 少扫一次，可接受。
#
# KEYS:
#   1. lease_key       — {ns}:{{worker_id}}:lease:data
#
# ARGV:
#   1. worker_id
#   2. current_lease_id    (空字符串表示首次)
#   3. new_lease_id        (服务端预生成)
#   4. now_ms
#   5. expires_at_ms
#   6. metrics_json        ("" 表示不更新 metrics)
#
# 返回:
#   {final_lease_id, expires_at_ms, granted_at_ms, "new"|"renewed"}
# ---------------------------------------------------------------------------
_GRANT_LUA = """
local lease_key = KEYS[1]

local worker_id        = ARGV[1]
local current_lease_id = ARGV[2]
local new_lease_id     = ARGV[3]
local now_ms           = tonumber(ARGV[4])
local expires_at_ms    = tonumber(ARGV[5])
local metrics_json     = ARGV[6]

local stored_lease_id = redis.call('HGET', lease_key, 'lease_id')
local stored_expires  = tonumber(redis.call('HGET', lease_key, 'expires_at_ms') or "0")

local final_lease_id
local outcome

if stored_lease_id
   and current_lease_id ~= ''
   and stored_lease_id == current_lease_id
   and stored_expires > now_ms then
    -- 续租：lease_id 不变，只刷新过期时间
    final_lease_id = stored_lease_id
    outcome = 'renewed'
else
    -- 首次或重发：用新的 lease_id
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

-- TTL 兜底：即使 sweep loop 挂了，Hash 也会自动过期（多给 5 倍冗余）
local ttl_seconds = math.floor((expires_at_ms - now_ms) / 1000) + 5
if ttl_seconds < 1 then ttl_seconds = 1 end
redis.call('EXPIRE', lease_key, ttl_seconds)

return {final_lease_id, tostring(expires_at_ms), tostring(now_ms), outcome}
"""


# ---------------------------------------------------------------------------
# Lua 脚本：revoke lease_key 单 key 操作（T6-T1c）
#
# KEYS: 1. lease_key
# ARGV: (none)
# 返回 1 (曾经有 lease) 或 0
# ---------------------------------------------------------------------------
_REVOKE_LUA = """
local lease_key = KEYS[1]
local existed = redis.call('EXISTS', lease_key)
redis.call('DEL', lease_key)
return existed
"""


class LeaseStore:
    """基于 Redis 的 Worker Lease 状态机。

    所有 Key 都纳入 ``{namespace}:lease:*`` 前缀，与 ``RedisKeys`` /
    ``control_plane`` 现有命名空间约定一致。
    """

    # T6-T1c: lease_key 用 hash tag `{worker_id}` 让集群模式下同 worker
    # 数据落同 slot。Lua 脚本因此只操作单 key，跨 slot 的 index（zset/set）
    # 走 pipeline。
    LEASE_KEY_TEMPLATE = "{ns}:{{{worker_id}}}:lease:data"
    EXPIRING_ZSET_SUFFIX = "lease:expiring"
    ACTIVE_SET_SUFFIX = "lease:active"

    def __init__(
        self,
        redis_client: Any,
        namespace: str = "antcode",
        policy: LeasePolicy | None = None,
    ):
        """构造 LeaseStore。

        Args:
            redis_client: 已连接的 ``redis.asyncio.Redis`` 实例。
            namespace: Key 命名空间前缀（默认 ``antcode``）。
            policy: lease 时长策略；未提供则用默认 30s TTL / 10s 续租。
        """
        self._redis = redis_client
        self._namespace = (namespace or "antcode").strip() or "antcode"
        self._policy = policy or LeasePolicy()
        self._grant_script_sha: str | None = None
        self._revoke_script_sha: str | None = None
        # _ensure_scripts_loaded 用 Event 做快速路径(无锁开销),用 Lock 做
        # 慢路径串行化:第一次 SCRIPT LOAD 期间多个 grant/revoke 并发到达时,
        # 只让一个协程真正发 SCRIPT LOAD,其它人在锁外面等 Event。
        self._scripts_loaded = asyncio.Event()
        self._scripts_lock = asyncio.Lock()

    # ------------------------------------------------------------------ Keys

    def _lease_key(self, worker_id: str) -> str:
        # 输出 `{ns}:{worker_id}:lease:data`，worker_id 在 Redis hash tag `{}` 里
        return self.LEASE_KEY_TEMPLATE.format(ns=self._namespace, worker_id=worker_id)

    def _expiring_zset(self) -> str:
        return f"{self._namespace}:{self.EXPIRING_ZSET_SUFFIX}"

    def _active_set(self) -> str:
        return f"{self._namespace}:{self.ACTIVE_SET_SUFFIX}"

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
        """
        if not worker_id:
            raise ValueError("worker_id 不能为空")

        now_ms = int(time.time() * 1000)
        expires_at_ms = now_ms + int(self._policy.ttl_ms)
        new_lease_id = secrets.token_hex(16)

        metrics_json = ""
        if metrics:
            try:
                metrics_json = json.dumps(metrics, ensure_ascii=False, default=str)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(f"Lease grant: metrics 序列化失败，忽略: {exc}")
                metrics_json = ""

        # T6-T1c: Lua 只操作 lease_key 单 key（集群 slot 安全）
        keys = [self._lease_key(worker_id)]
        args = [
            worker_id,
            current_lease_id or "",
            new_lease_id,
            str(now_ms),
            str(expires_at_ms),
            metrics_json,
        ]
        result = await self._evalsha_grant(keys, args)

        # Lua 返回 [lease_id, expires_at_ms, granted_at_ms, outcome]
        final_lease_id = _to_str(result[0])
        final_expires = int(_to_str(result[1]))
        final_granted = int(_to_str(result[2]))
        outcome = _to_str(result[3]) if len(result) > 3 else ""

        # T6-T1c: 索引结构在 Lua 外用 pipeline 追加。集群模式下
        # pipeline(transaction=False) 会按 key 路由到对应节点；zset/set 是全局
        # 单 key（每个只有一个 slot），追加失败最坏是 sweep 少扫一次，
        # lease_key 的 EXPIRE 兜底自然过期。
        try:
            pipe = self._redis.pipeline(transaction=False)
            pipe.zadd(self._expiring_zset(), {worker_id: expires_at_ms})
            pipe.sadd(self._active_set(), worker_id)
            await pipe.execute()
        except Exception as exc:
            logger.warning(
                f"Lease index 更新失败（不阻断发租，靠 EXPIRE 兜底）: "
                f"worker_id={worker_id}, err={exc}"
            )

        if outcome == "new":
            logger.debug(
                f"Lease 新发: worker_id={worker_id}, lease_id={final_lease_id}, "
                f"expires_at_ms={final_expires}"
            )
        else:
            logger.debug(
                f"Lease 续租: worker_id={worker_id}, lease_id={final_lease_id}, "
                f"expires_at_ms={final_expires}"
            )

        return Lease(
            worker_id=worker_id,
            lease_id=final_lease_id,
            expires_at_ms=final_expires,
            granted_at_ms=final_granted,
        )

    async def revoke(self, worker_id: str, reason: str = "") -> bool:
        """Master / Gateway 主动撤销 Worker 的 lease。

        Args:
            worker_id: Worker 标识。
            reason: 仅用于日志，便于运维审计。

        Returns:
            True 表示曾经有 lease 被撤销，False 表示本来就没 lease。
        """
        if not worker_id:
            return False

        # T6-T1c: Lua 只删 lease_key 单 key
        keys = [self._lease_key(worker_id)]
        result = await self._evalsha_revoke(keys, [])
        revoked = int(result or 0) > 0
        # 索引清理走 pipeline（同 grant 的追加逻辑）
        try:
            pipe = self._redis.pipeline(transaction=False)
            pipe.zrem(self._expiring_zset(), worker_id)
            pipe.srem(self._active_set(), worker_id)
            await pipe.execute()
        except Exception as exc:
            logger.warning(
                f"Lease index 撤销失败（不阻断，靠 EXPIRE 兜底）: "
                f"worker_id={worker_id}, err={exc}"
            )
        if revoked:
            logger.info(f"Lease 已撤销: worker_id={worker_id}, reason={reason or 'unspecified'}")
        return revoked

    async def get(self, worker_id: str) -> Lease | None:
        """读取当前 lease 快照（不刷新过期时间）。"""
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

    async def list_active(self) -> list[str]:
        """返回当前所有持有 lease 的 worker_id。"""
        members = await self._redis.smembers(self._active_set())
        return sorted(_to_str(m) for m in members)

    async def sweep_expired(
        self,
        now_ms: int | None = None,
        batch: int = 100,
    ) -> list[str]:
        """扫描过期 lease，剔除离线 Worker。

        Args:
            now_ms: 截止时间（毫秒）；默认取当前时间。
            batch: 每轮 ZRANGEBYSCORE 的上限，避免单次清理过多 worker 阻塞 loop。

        Returns:
            本次被剔除的 ``worker_id`` 列表（按 expires_at 升序）。
        """
        if now_ms is None:
            now_ms = int(time.time() * 1000)

        expiring_key = self._expiring_zset()
        active_key = self._active_set()

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

        # 用 pipeline 一次清理 Hash + ZSet + Set
        pipe = self._redis.pipeline(transaction=False)
        for worker_id in worker_ids:
            pipe.delete(self._lease_key(worker_id))
            pipe.zrem(expiring_key, worker_id)
            pipe.srem(active_key, worker_id)
        await pipe.execute()

        logger.info(f"Lease sweep 剔除 {len(worker_ids)} 个过期 worker: {worker_ids}")
        return worker_ids


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------


def _to_str(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    if value is None:
        return ""
    return str(value)


__all__ = [
    "Lease",
    "LeasePolicy",
    "LeaseStore",
]
