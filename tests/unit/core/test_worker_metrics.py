import pytest
from antcode_core.application.services.workers.worker_metrics import normalize_worker_metrics


def test_zero_worker_metrics_remain_zero() -> None:
    metrics = normalize_worker_metrics(
        {
            "cpu_percent": 0,
            "memory_percent": 0,
            "disk_percent": 0,
            "running_tasks": 0,
            "max_concurrent_tasks": 4,
            "queued_tasks": 0,
        }
    )

    assert metrics == {
        "cpu": 0.0,
        "memory": 0.0,
        "disk": 0.0,
        "runningTasks": 0,
        "maxConcurrentTasks": 4,
        "queuedTasks": 0,
    }


def test_invalid_worker_metric_is_rejected() -> None:
    with pytest.raises(ValueError):
        normalize_worker_metrics({"cpu": "not-a-number"})
