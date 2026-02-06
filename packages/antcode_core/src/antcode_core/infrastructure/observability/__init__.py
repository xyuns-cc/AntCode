"""
Observability 模块

可观测性：
- metrics: Prometheus 指标
- health: 健康检查
- tracing: OpenTelemetry 链路追踪（可选）
"""

from antcode_core.infrastructure.observability.health import (
    HealthChecker,
    HealthCheckResult,
    HealthStatus,
    OverallHealth,
    check_database,
    check_redis,
    health_checker,
    register_default_checks,
)
from antcode_core.infrastructure.observability.metrics import (
    MetricsCollector,
    inc_counter,
    metrics,
    observe_histogram,
    set_gauge,
)
from antcode_core.infrastructure.observability.tracing import (
    Span,
    SpanContext,
    Tracer,
    init_tracing,
    tracer,
)

__all__ = [
    # Metrics
    "MetricsCollector",
    "metrics",
    "inc_counter",
    "set_gauge",
    "observe_histogram",
    # Health
    "HealthChecker",
    "HealthStatus",
    "HealthCheckResult",
    "OverallHealth",
    "health_checker",
    "check_redis",
    "check_database",
    "register_default_checks",
    # Tracing
    "Tracer",
    "Span",
    "SpanContext",
    "tracer",
    "init_tracing",
]
