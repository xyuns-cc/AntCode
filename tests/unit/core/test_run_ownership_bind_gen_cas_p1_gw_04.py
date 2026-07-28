"""Ownership generation persistence and monotonic fencing tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from antcode_core.application.services.workers import run_ownership_service as service
from antcode_core.domain.models import TaskRunLeaseGeneration
from antcode_core.domain.models.enums import TaskStatus

EXPECTED_TRANSITION_WRITES = 2


def _run(*, lease_id="lease-2", lease_gen=200):
    return SimpleNamespace(
        id=1,
        run_id="run-1",
        worker_id=42,
        lease_id=lease_id,
        lease_gen=lease_gen,
        status=TaskStatus.RUNNING,
    )


def test_monotonic_generation_rejects_older_or_colliding_lease() -> None:
    execution = _run()

    with pytest.raises(PermissionError, match="单调 CAS"):
        service._require_monotonic_generation(execution, "lease-1", 100)
    with pytest.raises(PermissionError, match="单调 CAS"):
        service._require_monotonic_generation(execution, "lease-other", 200)

    service._require_monotonic_generation(execution, "lease-2", 200)
    service._require_monotonic_generation(execution, "lease-3", 201)

    with pytest.raises(PermissionError, match="不允许复用"):
        service._require_monotonic_generation(execution, "lease-2", 201)


@pytest.mark.asyncio
async def test_transition_closes_previous_generation_and_opens_new() -> None:
    update_or_create = AsyncMock()
    with patch.object(TaskRunLeaseGeneration, "update_or_create", update_or_create):
        await service._record_generation_transition(
            execution=_run(),
            worker_id=42,
            lease_id="lease-3",
            lease_gen=201,
            log_cutoff_id="50-7",
            connection=object(),
        )

    assert update_or_create.await_count == EXPECTED_TRANSITION_WRITES
    previous = update_or_create.await_args_list[0].kwargs
    current = update_or_create.await_args_list[1].kwargs
    assert previous["lease_id"] == "lease-2"
    assert previous["defaults"]["log_valid_through_id"] == "50-7"
    assert current["lease_id"] == "lease-3"
    assert current["defaults"]["log_valid_through_id"] is None


@pytest.mark.asyncio
async def test_bind_rejects_invalid_generation_before_database_access() -> None:
    with pytest.raises(ValueError, match="正整数"):
        await service.bind_worker_run_lease_generation(
            "worker-1",
            "run-1",
            lease_id="lease-1",
            lease_gen=0,
            log_cutoff_id="0-0",
        )


@pytest.mark.asyncio
async def test_bind_rejects_invalid_log_cutoff_before_database_access() -> None:
    with pytest.raises(ValueError, match="Stream ID"):
        await service.bind_worker_run_lease_generation(
            "worker-1",
            "run-1",
            lease_id="lease-1",
            lease_gen=1,
            log_cutoff_id="invalid",
        )


def test_generation_history_model_has_required_fields() -> None:
    fields = TaskRunLeaseGeneration._meta.fields_map

    assert {"run_id", "worker_id", "lease_id", "lease_gen", "log_valid_through_id"} <= set(fields)
