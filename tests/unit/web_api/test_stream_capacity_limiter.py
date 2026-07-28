from types import SimpleNamespace
from unittest.mock import AsyncMock

import antcode_web_api.streams.stream_capacity_limiter as limiter_module
import pytest
from antcode_web_api.streams.stream_capacity_limiter import (
    LEASE_LIMIT_RUN,
    GlobalStreamLimitExceededError,
    RedisStreamCapacityLimiter,
    StreamCapacityLimits,
)


@pytest.mark.asyncio
async def test_acquire_uses_one_cluster_slot_and_global_limits(monkeypatch):
    redis = SimpleNamespace(eval=AsyncMock(return_value=[1, 0, 1, 1, 1]))
    monkeypatch.setattr(limiter_module, "get_redis_client", AsyncMock(return_value=redis))
    monkeypatch.setattr(limiter_module, "redis_namespace", lambda: "tenant")
    limiter = RedisStreamCapacityLimiter(lease_ttl_seconds=60)

    lease = await limiter.acquire("run-1", 7, StreamCapacityLimits(100, 10, 3))

    args = redis.eval.await_args.args
    assert args[1] == 3
    assert args[2:5] == (
        "{tenant}:sse:leases:total",
        "{tenant}:sse:leases:run:run-1",
        "{tenant}:sse:leases:user:7",
    )
    # 末位新增 key 物理 TTL 余量（P2 §4.2：一次性 run/user ZSET 防残留）
    assert args[-4:] == (100, 10, 3, limiter_module.KEY_TTL_MARGIN_MS)
    assert lease.run_id == "run-1"
    assert lease.user_id == 7


@pytest.mark.asyncio
async def test_acquire_exposes_rejected_dimension(monkeypatch):
    redis = SimpleNamespace(eval=AsyncMock(return_value=[0, 2, 4, 2, 1]))
    monkeypatch.setattr(limiter_module, "get_redis_client", AsyncMock(return_value=redis))
    limiter = RedisStreamCapacityLimiter(lease_ttl_seconds=60)

    with pytest.raises(GlobalStreamLimitExceededError) as raised:
        await limiter.acquire("run-1", 7, StreamCapacityLimits(100, 2, 3))

    assert raised.value.dimension == LEASE_LIMIT_RUN


@pytest.mark.asyncio
async def test_release_and_renew_use_the_same_three_keys(monkeypatch):
    redis = SimpleNamespace(eval=AsyncMock(side_effect=[[1, 0, 1, 1, 1], 1, 3]))
    monkeypatch.setattr(limiter_module, "get_redis_client", AsyncMock(return_value=redis))
    limiter = RedisStreamCapacityLimiter(lease_ttl_seconds=60)
    lease = await limiter.acquire("run-1", 7, StreamCapacityLimits(100, 10, 3))

    assert await limiter.renew(lease) is True
    await limiter.release(lease)

    acquire_keys = redis.eval.await_args_list[0].args[2:5]
    renew_keys = redis.eval.await_args_list[1].args[2:5]
    release_keys = redis.eval.await_args_list[2].args[2:5]
    assert acquire_keys == renew_keys == release_keys


def test_all_capacity_scripts_purge_expired_leases_before_counting():
    scripts = (
        limiter_module._ACQUIRE_LUA,
        limiter_module._RENEW_LUA,
        limiter_module._COUNTS_LUA,
    )
    assert all("redis.call('TIME')" in script for script in scripts)
    assert all("ZREMRANGEBYSCORE" in script for script in scripts)
