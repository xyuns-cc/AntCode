"""
弹性和容错模块

提供企业级的容错机制：
- 熔断器（Circuit Breaker）
- 健康检查聚合
"""

from antcode_core.infrastructure.resilience.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerError,
    CircuitOpenError,
    CircuitState,
    circuit_breaker,
)

__all__ = [
    # 熔断器
    "CircuitBreaker",
    "CircuitState",
    "CircuitBreakerConfig",
    "CircuitBreakerError",
    "CircuitOpenError",
    "circuit_breaker",
]
