"""
弹性和容错模块

提供企业级的容错机制：
- 熔断器（Circuit Breaker）
- 服务降级策略
- 健康检查聚合
"""

from .circuit_breaker import (
    CircuitBreaker,
    CircuitState,
    CircuitBreakerConfig,
    CircuitBreakerError,
    CircuitOpenError,
    circuit_breaker,
)
from .fallback import (
    FallbackStrategy,
    CacheFallback,
    DefaultValueFallback,
    fallback,
)

__all__ = [
    # 熔断器
    "CircuitBreaker",
    "CircuitState",
    "CircuitBreakerConfig",
    "CircuitBreakerError",
    "CircuitOpenError",
    "circuit_breaker",
    # 降级策略
    "FallbackStrategy",
    "CacheFallback",
    "DefaultValueFallback",
    "fallback",
]
