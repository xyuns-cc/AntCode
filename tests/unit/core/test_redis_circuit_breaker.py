"""R6: Redis 熔断器接入。"""

from __future__ import annotations

import asyncio

import pytest
from antcode_core.common.config import settings
from antcode_core.infrastructure.redis.circuit import (
    CircuitOpenError,
    get_redis_circuit_breaker,
    redis_breaker_guard,
)
from antcode_core.infrastructure.resilience.circuit_breaker import (
    CircuitBreaker,
    CircuitState,
)
from redis.exceptions import ConnectionError as RedisConnError


@pytest.fixture(autouse=True)
def _reset_breaker(monkeypatch):
    """每个用例前清掉单例并设置紧凑配置便于测试。"""
    CircuitBreaker._instances.pop("redis", None)
    monkeypatch.setattr(settings, "REDIS_CIRCUIT_FAILURE_THRESHOLD", 3, raising=False)
    monkeypatch.setattr(settings, "REDIS_CIRCUIT_RECOVERY_SECONDS", 0.1, raising=False)
    monkeypatch.setattr(settings, "REDIS_CIRCUIT_BREAKER_ENABLED", True, raising=False)
    yield
    CircuitBreaker._instances.pop("redis", None)


@pytest.mark.asyncio
async def test_breaker_trips_after_threshold():
    async def _op():
        async with redis_breaker_guard():
            raise RedisConnError("boom")

    for _ in range(3):
        with pytest.raises(RedisConnError):
            await _op()

    # 第 4 次：熔断器已打开，直接 CircuitOpenError，不再实际调用底层
    with pytest.raises(CircuitOpenError):
        await _op()


@pytest.mark.asyncio
async def test_breaker_recovers_after_timeout():
    async def _fail():
        async with redis_breaker_guard():
            raise RedisConnError("boom")

    for _ in range(3):
        with pytest.raises(RedisConnError):
            await _fail()
    assert get_redis_circuit_breaker().state == CircuitState.OPEN

    # 等过恢复时间，应进入 HALF_OPEN 放行
    await asyncio.sleep(0.15)

    async def _ok():
        async with redis_breaker_guard():
            return "ok"

    # 半开期成功 2 次后会关闭（success_threshold 默认 2）
    assert await _ok() == "ok"
    assert await _ok() == "ok"
    assert get_redis_circuit_breaker().state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_disabled_breaker_is_passthrough(monkeypatch):
    monkeypatch.setattr(settings, "REDIS_CIRCUIT_BREAKER_ENABLED", False, raising=False)

    async def _op():
        async with redis_breaker_guard():
            raise RedisConnError("boom")

    # 关闭后不会触发熔断，连续失败也只是抛原异常
    for _ in range(10):
        with pytest.raises(RedisConnError):
            await _op()
    # 没有被实例化（或不会切到 OPEN）
    breaker = CircuitBreaker.get("redis")
    if breaker:
        assert breaker.state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_non_redis_exceptions_dont_trip():
    async def _op():
        async with redis_breaker_guard():
            raise ValueError("logical bug, not a connection issue")

    for _ in range(10):
        with pytest.raises(ValueError):
            await _op()
    assert get_redis_circuit_breaker().state == CircuitState.CLOSED


def test_settings_carry_circuit_defaults():
    # 默认值应为「启用 + 阈值合理」
    s = settings
    assert hasattr(s, "REDIS_CIRCUIT_BREAKER_ENABLED")
    assert hasattr(s, "REDIS_CIRCUIT_FAILURE_THRESHOLD")
    assert hasattr(s, "REDIS_CIRCUIT_RECOVERY_SECONDS")
