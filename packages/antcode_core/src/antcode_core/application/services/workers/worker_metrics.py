"""Strict normalization for Worker load metrics."""

from collections.abc import Mapping
from typing import Any


def _value(metrics: Mapping[str, Any], names: tuple[str, ...], default: int) -> Any:
    for name in names:
        if name in metrics and metrics[name] not in (None, ""):
            return metrics[name]
    return default


def normalize_worker_metrics(metrics: Mapping[str, Any]) -> dict[str, float | int]:
    """Normalize aliases while preserving legitimate zero values."""
    return {
        "cpu": float(_value(metrics, ("cpu", "cpu_percent"), 100)),
        "memory": float(_value(metrics, ("memory", "memory_percent"), 100)),
        "disk": float(_value(metrics, ("disk", "disk_percent"), 100)),
        "runningTasks": int(_value(metrics, ("runningTasks", "running_tasks"), 0)),
        "maxConcurrentTasks": int(_value(metrics, ("maxConcurrentTasks", "max_concurrent_tasks"), 1)),
        "queuedTasks": int(_value(metrics, ("queuedTasks", "queued_tasks"), 0)),
    }


__all__ = ["normalize_worker_metrics"]
