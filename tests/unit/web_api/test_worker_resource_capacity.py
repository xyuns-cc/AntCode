import pytest
from antcode_core.domain.schemas.worker import WorkerMetrics
from antcode_web_api.routes.v1.workers_resources import (
    BYTES_PER_GIB,
    BYTES_PER_MIB,
    DISK_TOTAL,
    MEMORY_USED,
    _capacity_value,
)

EXPECTED_MIB = 2.0
EXPECTED_GIB = 3.0


def test_capacity_uses_current_heartbeat_byte_fields() -> None:
    resources = {"memoryUsed": EXPECTED_MIB * BYTES_PER_MIB, "memory_used_mb": 999}

    assert _capacity_value(resources, MEMORY_USED) == EXPECTED_MIB


def test_capacity_keeps_legacy_heartbeat_compatibility() -> None:
    resources = {"disk_total_gb": EXPECTED_GIB}

    assert _capacity_value(resources, DISK_TOTAL) == EXPECTED_GIB


def test_capacity_conversion_handles_integer_gib() -> None:
    resources = {"diskTotal": int(EXPECTED_GIB * BYTES_PER_GIB)}

    assert _capacity_value(resources, DISK_TOTAL) == pytest.approx(EXPECTED_GIB)


@pytest.mark.parametrize(
    "field",
    ["cpuCores", "memoryTotal", "memoryUsed", "memoryAvailable", "diskTotal", "diskUsed", "diskFree"],
)
def test_worker_metrics_rejects_negative_capacity(field: str) -> None:
    with pytest.raises(ValueError):
        WorkerMetrics.model_validate({field: -1})
