"""
指标收集

Requirements: 12.1
"""

import time
from typing import Any

try:
    import psutil
except ImportError:
    psutil = None


class MetricsCollector:
    """
    指标收集器

    收集系统和 Worker 指标，支持 Prometheus 格式导出。

    Requirements: 12.1
    """

    def __init__(self):
        self._counters: dict[str, int] = {}
        self._gauges: dict[str, float] = {}
        self._start_time = time.time()

    def inc(self, name: str, value: int = 1) -> None:
        """增加计数器"""
        self._counters[name] = self._counters.get(name, 0) + value

    def set(self, name: str, value: float) -> None:
        """设置仪表值"""
        self._gauges[name] = value

    def get_system_metrics(self) -> dict[str, Any]:
        """获取系统指标"""
        if not psutil:
            return {}

        return {
            "cpu_percent": psutil.cpu_percent(),
            "memory_percent": psutil.virtual_memory().percent,
            "disk_percent": psutil.disk_usage("/").percent,
        }

    def get_all(self) -> dict[str, Any]:
        """获取所有指标"""
        metrics = {
            "uptime_seconds": time.time() - self._start_time,
            **self._counters,
            **self._gauges,
            **self.get_system_metrics(),
        }
        return metrics

    def to_prometheus(self) -> str:
        """导出 Prometheus 格式"""
        lines = []
        metrics = self.get_all()

        for name, value in metrics.items():
            metric_name = f"antcode_worker_{name}"
            if isinstance(value, (int, float)):
                lines.append(f"{metric_name} {value}")

        return "\n".join(lines)
