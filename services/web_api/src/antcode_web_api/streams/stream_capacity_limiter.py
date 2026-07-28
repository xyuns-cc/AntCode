"""Redis-backed global capacity leases for SSE subscriptions."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any, Protocol, cast

from antcode_core.infrastructure.redis import get_redis_client, redis_namespace

LEASE_LIMIT_TOTAL = 1
LEASE_LIMIT_RUN = 2
LEASE_LIMIT_USER = 3

_ACQUIRE_LUA = """
local clock = redis.call('TIME')
local now = tonumber(clock[1]) * 1000 + math.floor(tonumber(clock[2]) / 1000)
for index = 1, 3 do
    redis.call('ZREMRANGEBYSCORE', KEYS[index], '-inf', now)
end
local total = redis.call('ZCARD', KEYS[1])
local per_run = redis.call('ZCARD', KEYS[2])
local per_user = redis.call('ZCARD', KEYS[3])
if total >= tonumber(ARGV[3]) then return {0, 1, total, per_run, per_user} end
if per_run >= tonumber(ARGV[4]) then return {0, 2, total, per_run, per_user} end
if per_user >= tonumber(ARGV[5]) then return {0, 3, total, per_run, per_user} end
for index = 1, 3 do
    redis.call('ZADD', KEYS[index], now + tonumber(ARGV[2]), ARGV[1])
    -- P2 §4.2: run/user 维度 ZSET 是一次性 key；进程崩溃后没人再访问就
    -- 永久残留。给 key 本身设 lease TTL + 余量的过期，成员逻辑过期由
    -- ZREMRANGEBYSCORE 处理，key 物理过期由 Redis 兜底。
    redis.call('PEXPIRE', KEYS[index], tonumber(ARGV[2]) + tonumber(ARGV[6]))
end
return {1, 0, total + 1, per_run + 1, per_user + 1}
"""

_RENEW_LUA = """
local clock = redis.call('TIME')
local now = tonumber(clock[1]) * 1000 + math.floor(tonumber(clock[2]) / 1000)
for index = 1, 3 do
    redis.call('ZREMRANGEBYSCORE', KEYS[index], '-inf', now)
    if not redis.call('ZSCORE', KEYS[index], ARGV[1]) then return 0 end
end
for index = 1, 3 do
    redis.call('ZADD', KEYS[index], now + tonumber(ARGV[2]), ARGV[1])
    redis.call('PEXPIRE', KEYS[index], tonumber(ARGV[2]) + tonumber(ARGV[3]))
end
return 1
"""

# key 物理 TTL 余量：最后一个 lease 逻辑过期后，key 最多再存活这么久。
KEY_TTL_MARGIN_MS = 60_000

_RELEASE_LUA = """
local removed = 0
for index = 1, 3 do
    removed = removed + redis.call('ZREM', KEYS[index], ARGV[1])
end
return removed
"""

_COUNTS_LUA = """
local clock = redis.call('TIME')
local now = tonumber(clock[1]) * 1000 + math.floor(tonumber(clock[2]) / 1000)
for index = 1, 3 do
    redis.call('ZREMRANGEBYSCORE', KEYS[index], '-inf', now)
end
return {
    redis.call('ZCARD', KEYS[1]),
    redis.call('ZCARD', KEYS[2]),
    redis.call('ZCARD', KEYS[3])
}
"""


class GlobalStreamLimitExceededError(Exception):
    """A global SSE subscription dimension has reached its limit."""

    def __init__(self, dimension: int) -> None:
        super().__init__(dimension)
        self.dimension = dimension


@dataclass(frozen=True)
class StreamCapacityLease:
    token: str
    run_id: str
    user_id: int


@dataclass(frozen=True)
class StreamCapacityLimits:
    total: int
    per_run: int
    per_user: int


class StreamCapacityLimiter(Protocol):
    async def ensure_capacity(
        self,
        run_id: str,
        user_id: int,
        limits: StreamCapacityLimits,
    ) -> None: ...

    async def acquire(
        self,
        run_id: str,
        user_id: int,
        limits: StreamCapacityLimits,
    ) -> StreamCapacityLease: ...

    async def renew(self, lease: StreamCapacityLease) -> bool: ...

    async def release(self, lease: StreamCapacityLease) -> None: ...

    async def counts(self, run_id: str, user_id: int) -> tuple[int, int, int]: ...


class RedisStreamCapacityLimiter:
    """Maintains globally shared expiring leases in three Redis sorted sets."""

    def __init__(self, *, lease_ttl_seconds: int) -> None:
        self._lease_ttl_ms = lease_ttl_seconds * 1000

    async def ensure_capacity(
        self,
        run_id: str,
        user_id: int,
        limits: StreamCapacityLimits,
    ) -> None:
        counts = await self.counts(run_id, user_id)
        dimension = _exceeded_dimension(counts, limits)
        if dimension:
            raise GlobalStreamLimitExceededError(dimension)

    async def acquire(
        self,
        run_id: str,
        user_id: int,
        limits: StreamCapacityLimits,
    ) -> StreamCapacityLease:
        lease = StreamCapacityLease(uuid.uuid4().hex, run_id, user_id)
        result = await self._eval(
            _ACQUIRE_LUA,
            self._keys(lease),
            lease.token,
            self._lease_ttl_ms,
            limits.total,
            limits.per_run,
            limits.per_user,
            KEY_TTL_MARGIN_MS,
        )
        if int(result[0]) != 1:
            raise GlobalStreamLimitExceededError(int(result[1]))
        return lease

    async def renew(self, lease: StreamCapacityLease) -> bool:
        result = await self._eval(
            _RENEW_LUA,
            self._keys(lease),
            lease.token,
            self._lease_ttl_ms,
            KEY_TTL_MARGIN_MS,
        )
        return int(result) == 1

    async def release(self, lease: StreamCapacityLease) -> None:
        await self._eval(_RELEASE_LUA, self._keys(lease), lease.token)

    async def counts(self, run_id: str, user_id: int) -> tuple[int, int, int]:
        lease = StreamCapacityLease("capacity-check", run_id, user_id)
        result = await self._eval(_COUNTS_LUA, self._keys(lease))
        return int(result[0]), int(result[1]), int(result[2])

    async def _eval(self, script: str, keys: tuple[str, str, str], *args):
        redis = await get_redis_client()
        return await cast(Awaitable[Any], redis.eval(script, len(keys), *keys, *args))

    def _keys(self, lease: StreamCapacityLease) -> tuple[str, str, str]:
        prefix = f"{{{redis_namespace()}}}:sse:leases"
        return (
            f"{prefix}:total",
            f"{prefix}:run:{lease.run_id}",
            f"{prefix}:user:{lease.user_id}",
        )


def _exceeded_dimension(
    counts: tuple[int, int, int],
    limits: StreamCapacityLimits,
) -> int | None:
    total, per_run, per_user = counts
    if total >= limits.total:
        return LEASE_LIMIT_TOTAL
    if per_run >= limits.per_run:
        return LEASE_LIMIT_RUN
    if per_user >= limits.per_user:
        return LEASE_LIMIT_USER
    return None


__all__ = [
    "GlobalStreamLimitExceededError",
    "LEASE_LIMIT_RUN",
    "LEASE_LIMIT_TOTAL",
    "LEASE_LIMIT_USER",
    "RedisStreamCapacityLimiter",
    "StreamCapacityLimiter",
    "StreamCapacityLease",
    "StreamCapacityLimits",
]
