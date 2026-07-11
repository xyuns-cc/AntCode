"""Redis token bucket tests."""

from unittest.mock import AsyncMock

import pytest
from antcode_gateway.redis_token_bucket import RedisTokenBucketLimiter


@pytest.mark.asyncio
async def test_limiter_uses_atomic_redis_script_and_stable_hashed_key():
    redis = AsyncMock()
    redis.script_load.return_value = "sha-1"
    redis.evalsha.return_value = [1, b"4.5", b"10.0", b"0"]
    limiter = RedisTokenBucketLimiter(
        redis,
        rate=2.0,
        capacity=5,
        key_prefix="ac:gateway:rate-limit",
    )

    result = await limiter.allow("worker-1")

    assert result.allowed is True
    assert result.remaining == 4
    args = redis.evalsha.await_args.args
    assert args[0:2] == ("sha-1", 1)
    assert args[2].startswith("ac:gateway:rate-limit:")
    assert "worker-1" not in args[2]


@pytest.mark.asyncio
async def test_redis_failure_is_retried_once_then_exposed():
    redis = AsyncMock()
    redis.script_load.side_effect = ["sha-1", "sha-2"]
    redis.evalsha.side_effect = [RuntimeError("NOSCRIPT"), RuntimeError("redis unavailable")]
    limiter = RedisTokenBucketLimiter(
        redis,
        rate=1.0,
        capacity=1,
        key_prefix="ac:gateway:rate-limit",
    )

    with pytest.raises(RuntimeError, match="redis unavailable"):
        await limiter.allow("worker-1")

    assert redis.evalsha.await_count == 2
