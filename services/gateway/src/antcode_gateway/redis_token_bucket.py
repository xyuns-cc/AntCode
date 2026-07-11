"""Redis-backed token bucket rate limiting."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any, Protocol, cast

MIN_KEY_TTL_SECONDS = 60

_TOKEN_BUCKET_SCRIPT = """
local key = KEYS[1]
local rate = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])
local clock = redis.call('TIME')
local now = tonumber(clock[1]) + tonumber(clock[2]) / 1000000
local state = redis.call('HMGET', key, 'tokens', 'updated_at')
local tokens = tonumber(state[1]) or capacity
local updated_at = tonumber(state[2]) or now
local elapsed = math.max(0, now - updated_at)
tokens = math.min(capacity, tokens + elapsed * rate)

local allowed = 0
if tokens >= 1 then
    tokens = tokens - 1
    allowed = 1
end

local retry_after = 0
local reset_at = 0
if rate > 0 then
    retry_after = math.max(0, (1 - tokens) / rate)
    reset_at = now + (capacity - tokens) / rate
end

redis.call('HSET', key, 'tokens', tostring(tokens), 'updated_at', tostring(now))
redis.call('EXPIRE', key, ttl)
return {allowed, tostring(tokens), tostring(reset_at), tostring(retry_after)}
"""


class RedisScriptClient(Protocol):
    async def script_load(self, script: str) -> str: ...

    def evalsha(self, sha: str, numkeys: int, *args: Any) -> Awaitable[list[Any]]: ...


@dataclass(frozen=True)
class RedisTokenBucketResult:
    allowed: bool
    remaining: int
    reset_at: float
    retry_after: float


class RedisTokenBucketLimiter:
    def __init__(
        self,
        redis_client: RedisScriptClient,
        *,
        rate: float,
        capacity: int,
        key_prefix: str,
    ) -> None:
        if rate < 0 or capacity < 1:
            raise ValueError("rate must be >= 0 and capacity must be >= 1")
        self._redis = redis_client
        self._rate = rate
        self._capacity = capacity
        self._key_prefix = key_prefix
        self._script_sha: str | None = None

    async def allow(self, identifier: str) -> RedisTokenBucketResult:
        result = await self._run_script(self._key(identifier))
        return RedisTokenBucketResult(
            allowed=int(result[0]) == 1,
            remaining=max(0, int(float(_text(result[1])))),
            reset_at=float(_text(result[2])),
            retry_after=float(_text(result[3])),
        )

    async def _run_script(self, key: str) -> list[Any]:
        sha = await self._ensure_script()
        args = (key, self._rate, self._capacity, self._key_ttl_seconds())
        try:
            return await cast(Awaitable[list[Any]], self._redis.evalsha(sha, 1, *args))
        except Exception:
            self._script_sha = None
            sha = await self._ensure_script()
            return await cast(Awaitable[list[Any]], self._redis.evalsha(sha, 1, *args))

    async def _ensure_script(self) -> str:
        if self._script_sha is None:
            self._script_sha = await self._redis.script_load(_TOKEN_BUCKET_SCRIPT)
        return self._script_sha

    def _key_ttl_seconds(self) -> int:
        if self._rate == 0:
            return MIN_KEY_TTL_SECONDS
        refill_seconds = self._capacity / self._rate
        return max(MIN_KEY_TTL_SECONDS, int(refill_seconds * 2) + 1)

    def _key(self, identifier: str) -> str:
        digest = hashlib.sha256(identifier.encode("utf-8")).hexdigest()
        return f"{self._key_prefix}:{digest}"


def _text(value: Any) -> str:
    return value.decode("ascii") if isinstance(value, bytes) else str(value)


__all__ = ["RedisTokenBucketLimiter", "RedisTokenBucketResult"]
