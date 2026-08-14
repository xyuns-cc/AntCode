"""Redis 共享熔断器单例。

将既有的 ``CircuitBreaker`` 封装为 ``redis_circuit`` 单例供 client / transport
层共用，达到「连续 N 次连接/操作失败后快速失败、过段时间再放行」的目的。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from redis.exceptions import ConnectionError as RedisConnError
from redis.exceptions import TimeoutError as RedisTimeoutError

from antcode_core.common.exceptions import RedisConnectionError
from antcode_core.common.settings_ref import current_settings
from antcode_core.infrastructure.resilience.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitOpenError,
)

_BREAKER_NAME = "redis"
_TRIPPING_EXCEPTIONS = {RedisConnError, RedisTimeoutError, RedisConnectionError}


def get_redis_circuit_breaker() -> CircuitBreaker:
    """获取（或创建）Redis 熔断器单例，配置从 Settings 拉取。"""
    settings = current_settings()
    existing = CircuitBreaker.get(_BREAKER_NAME)
    if existing is not None:
        return existing
    config = CircuitBreakerConfig(
        failure_threshold=settings.REDIS_CIRCUIT_FAILURE_THRESHOLD,
        timeout=settings.REDIS_CIRCUIT_RECOVERY_SECONDS,
        exception_types=_TRIPPING_EXCEPTIONS,
    )
    return CircuitBreaker(_BREAKER_NAME, config)


@asynccontextmanager
async def redis_breaker_guard() -> AsyncIterator[CircuitBreaker | None]:
    """围绕 Redis 操作的轻量保护。

    - ``REDIS_CIRCUIT_BREAKER_ENABLED=false`` 时直接 passthrough，行为完全等价旧代码
    - 否则进入熔断器：OPEN 状态会立刻 raise ``CircuitOpenError``，
      抛出的连接异常会计入失败统计
    """
    if not current_settings().REDIS_CIRCUIT_BREAKER_ENABLED:
        yield None
        return
    breaker = get_redis_circuit_breaker()
    async with breaker:
        yield breaker


__all__ = [
    "CircuitOpenError",
    "get_redis_circuit_breaker",
    "redis_breaker_guard",
]
