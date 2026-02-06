"""Prometheus 指标模块

提供指标收集和导出功能。
"""

from typing import Any


class MetricsCollector:
    """指标收集器

    简单的指标收集实现，可扩展为 Prometheus 客户端。
    """

    def __init__(self):
        self._counters: dict[str, int] = {}
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, list[float]] = {}

    def inc_counter(self, name: str, value: int = 1, labels: dict | None = None) -> None:
        """增加计数器"""
        key = self._make_key(name, labels)
        self._counters[key] = self._counters.get(key, 0) + value

    def set_gauge(self, name: str, value: float, labels: dict | None = None) -> None:
        """设置仪表值"""
        key = self._make_key(name, labels)
        self._gauges[key] = value

    def observe_histogram(
        self, name: str, value: float, labels: dict | None = None
    ) -> None:
        """记录直方图观测值"""
        key = self._make_key(name, labels)
        if key not in self._histograms:
            self._histograms[key] = []
        self._histograms[key].append(value)

    def _make_key(self, name: str, labels: dict | None) -> str:
        """生成指标键"""
        if not labels:
            return name
        label_str = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        return f"{name}{{{label_str}}}"

    def get_counter(self, name: str, labels: dict | None = None) -> int:
        """获取计数器值"""
        key = self._make_key(name, labels)
        return self._counters.get(key, 0)

    def get_gauge(self, name: str, labels: dict | None = None) -> float:
        """获取仪表值"""
        key = self._make_key(name, labels)
        return self._gauges.get(key, 0.0)

    def get_all_metrics(self) -> dict[str, Any]:
        """获取所有指标"""
        return {
            "counters": dict(self._counters),
            "gauges": dict(self._gauges),
            "histograms": {k: len(v) for k, v in self._histograms.items()},
        }

    def reset(self) -> None:
        """重置所有指标"""
        self._counters.clear()
        self._gauges.clear()
        self._histograms.clear()


# 全局指标收集器
metrics = MetricsCollector()


# 便捷函数
def inc_counter(name: str, value: int = 1, labels: dict | None = None) -> None:
    """增加计数器"""
    metrics.inc_counter(name, value, labels)


def set_gauge(name: str, value: float, labels: dict | None = None) -> None:
    """设置仪表值"""
    metrics.set_gauge(name, value, labels)


def observe_histogram(name: str, value: float, labels: dict | None = None) -> None:
    """记录直方图观测值"""
    metrics.observe_histogram(name, value, labels)
