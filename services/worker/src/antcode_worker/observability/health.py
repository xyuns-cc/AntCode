"""
健康检查

Requirements: 12.2
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class HealthStatus(str, Enum):
    """健康状态"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass
class HealthResult:
    """健康检查结果"""
    status: HealthStatus
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)


class HealthChecker:
    """
    健康检查器

    提供 K8s liveness 和 readiness 探针。

    Requirements: 12.2
    """

    def __init__(self):
        self._checks: dict[str, Callable[[], HealthResult]] = {}
        self._ready = False

    def register(self, name: str, check: Callable[[], HealthResult]) -> None:
        """注册健康检查"""
        self._checks[name] = check

    def set_ready(self, ready: bool) -> None:
        """设置就绪状态"""
        self._ready = ready

    def liveness(self) -> HealthResult:
        """
        存活探针

        检查进程是否存活，失败会触发重启。
        """
        # 基本存活检查
        return HealthResult(
            status=HealthStatus.HEALTHY,
            message="alive",
        )

    def readiness(self) -> HealthResult:
        """
        就绪探针

        检查是否可以接收流量。
        """
        if not self._ready:
            return HealthResult(
                status=HealthStatus.UNHEALTHY,
                message="not ready",
            )

        # 运行所有检查
        results = {}
        overall = HealthStatus.HEALTHY

        for name, check in self._checks.items():
            try:
                result = check()
                results[name] = result.status.value
                if result.status == HealthStatus.UNHEALTHY:
                    overall = HealthStatus.UNHEALTHY
                elif result.status == HealthStatus.DEGRADED and overall == HealthStatus.HEALTHY:
                    overall = HealthStatus.DEGRADED
            except Exception as e:
                results[name] = f"error: {e}"
                overall = HealthStatus.UNHEALTHY

        return HealthResult(
            status=overall,
            message="ready" if overall == HealthStatus.HEALTHY else "degraded",
            details=results,
        )
