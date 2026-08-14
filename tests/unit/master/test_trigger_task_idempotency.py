"""P1-DB-01: outbox ``task_trigger`` 重放不得重复触发任务。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from antcode_master.control.scheduler_loop import SchedulerService
from antcode_master.control.trigger_idempotency import TRIGGER_RUN_NAMESPACE, TriggerDeferred


def _service() -> SchedulerService:
    service = SchedulerService.__new__(SchedulerService)
    service.scheduler = MagicMock(spec=["get_job", "add_job"])
    service._execute_task = AsyncMock()
    service._resume_idempotent_trigger = AsyncMock()
    return service


_RUNS_PATH = "antcode_master.control.trigger_idempotency.TaskRun"


@pytest.mark.asyncio
async def test_replay_with_existing_run_does_not_schedule_again():
    service = _service()
    expected_run_id = str(uuid.uuid5(TRIGGER_RUN_NAMESPACE, "42:outbox-1"))
    existing = SimpleNamespace(run_id=expected_run_id)
    service._resume_idempotent_trigger.return_value = expected_run_id

    with patch(f"{_RUNS_PATH}.get_or_none", AsyncMock(return_value=existing)):
        await service.trigger_task(42, idempotency_key="outbox-1")

    service._resume_idempotent_trigger.assert_awaited_once_with(42, existing)
    service._execute_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_first_delivery_creates_durable_run_before_returning():
    service = _service()
    expected_run_id = str(uuid.uuid5(TRIGGER_RUN_NAMESPACE, "42:outbox-1"))
    service._execute_task.return_value = expected_run_id

    with (
        patch(f"{_RUNS_PATH}.get_or_none", AsyncMock(return_value=None)),
        patch(
            "antcode_master.control.trigger_idempotency.Task.get_or_none",
            AsyncMock(return_value=SimpleNamespace(is_active=True)),
        ),
    ):
        run_id = await service.trigger_task(42, idempotency_key="outbox-1")

    assert run_id == expected_run_id
    service._execute_task.assert_awaited_once_with(42, fixed_run_id=expected_run_id)
    service.scheduler.add_job.assert_not_called()


@pytest.mark.asyncio
async def test_busy_trigger_remains_deferred_instead_of_completing():
    service = _service()
    service._execute_task.return_value = None

    with (
        patch(f"{_RUNS_PATH}.get_or_none", AsyncMock(return_value=None)),
        patch(
            "antcode_master.control.trigger_idempotency.Task.get_or_none",
            AsyncMock(return_value=SimpleNamespace(is_active=True)),
        ),
        pytest.raises(TriggerDeferred, match="不可接纳"),
    ):
        await service.trigger_task(42, idempotency_key="outbox-1")


@pytest.mark.asyncio
async def test_trigger_without_key_keeps_legacy_behavior():
    service = _service()
    service.scheduler.get_job = MagicMock(return_value=None)

    await service.trigger_task(42)

    kwargs = service.scheduler.add_job.call_args.kwargs
    assert kwargs["id"].startswith("42_manual_")
    assert kwargs["kwargs"] == {"task_id": 42}
