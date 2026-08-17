"""P1-DB-01: outbox ``task_trigger`` 重放不得重复触发任务。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from antcode_core.application.services.scheduler.trigger_identity import scheduled_run_id
from antcode_core.domain.models.enums import ScheduleType
from antcode_master.control.scheduler_loop import SchedulerService
from antcode_master.control.trigger_idempotency import TRIGGER_RUN_NAMESPACE, TriggerDeferred

CREATED_AT = datetime(2026, 8, 17, 3, 4, 5, tzinfo=UTC)


def _service() -> SchedulerService:
    service = SchedulerService.__new__(SchedulerService)
    service.scheduler = MagicMock(spec=["get_job", "add_job"])
    service._execute_task = AsyncMock()
    service._resume_idempotent_trigger = AsyncMock()
    return service


def _task(schedule_type: ScheduleType = ScheduleType.CRON, *, is_active: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        id=42,
        schedule_type=schedule_type,
        scheduled_time=None,
        created_at=CREATED_AT,
        is_active=is_active,
    )


_RUNS_PATH = "antcode_master.control.trigger_idempotency.TaskRun"
_TASK_PATH = "antcode_master.control.trigger_idempotency.Task.get_or_none"


@pytest.mark.asyncio
async def test_replay_with_existing_run_does_not_schedule_again():
    service = _service()
    expected_run_id = str(uuid.uuid5(TRIGGER_RUN_NAMESPACE, "42:outbox-1"))
    existing = SimpleNamespace(run_id=expected_run_id)
    service._resume_idempotent_trigger.return_value = expected_run_id

    with (
        patch(_TASK_PATH, AsyncMock(return_value=_task())),
        patch(f"{_RUNS_PATH}.get_or_none", AsyncMock(return_value=existing)),
    ):
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
        patch(_TASK_PATH, AsyncMock(return_value=_task())),
    ):
        run_id = await service.trigger_task(42, idempotency_key="outbox-1")

    assert run_id == expected_run_id
    service._execute_task.assert_awaited_once_with(42, fixed_run_id=expected_run_id)
    service.scheduler.add_job.assert_not_called()


@pytest.mark.asyncio
async def test_one_time_trigger_dispatches_into_the_durable_schedule_slot():
    """一次性任务的手动触发必须复用耐久扫描的 run 身份（去重域绑死）。"""
    service = _service()
    task = _task(ScheduleType.ONCE)
    expected_run_id = scheduled_run_id(task)
    service._execute_task.return_value = expected_run_id

    with (
        patch(f"{_RUNS_PATH}.get_or_none", AsyncMock(return_value=None)),
        patch(_TASK_PATH, AsyncMock(return_value=task)),
    ):
        run_id = await service.trigger_task(42, idempotency_key="outbox-1")

    assert run_id == expected_run_id
    assert expected_run_id != str(uuid.uuid5(TRIGGER_RUN_NAMESPACE, "42:outbox-1"))
    service._execute_task.assert_awaited_once_with(42, fixed_run_id=expected_run_id)


@pytest.mark.asyncio
async def test_trigger_of_completed_one_time_task_folds_onto_its_run():
    """槽位已被耐久扫描占用并关闭（is_active=False）时，触发折叠而不是永久 defer。"""
    service = _service()
    task = _task(ScheduleType.ONCE, is_active=False)
    existing = SimpleNamespace(run_id=scheduled_run_id(task))
    service._resume_idempotent_trigger.return_value = existing.run_id

    with (
        patch(_TASK_PATH, AsyncMock(return_value=task)),
        patch(f"{_RUNS_PATH}.get_or_none", AsyncMock(return_value=existing)),
    ):
        run_id = await service.trigger_task(42, idempotency_key="outbox-1")

    assert run_id == existing.run_id
    service._execute_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_busy_trigger_remains_deferred_instead_of_completing():
    service = _service()
    service._execute_task.return_value = None

    with (
        patch(f"{_RUNS_PATH}.get_or_none", AsyncMock(return_value=None)),
        patch(_TASK_PATH, AsyncMock(return_value=_task())),
        pytest.raises(TriggerDeferred, match="不可接纳"),
    ):
        await service.trigger_task(42, idempotency_key="outbox-1")


@pytest.mark.asyncio
async def test_missing_trigger_target_is_reported_before_run_lookup():
    service = _service()
    runs = AsyncMock()

    with (
        patch(_TASK_PATH, AsyncMock(return_value=None)),
        patch(f"{_RUNS_PATH}.get_or_none", runs),
        pytest.raises(LookupError, match="目标不存在"),
    ):
        await service.trigger_task(42, idempotency_key="outbox-1")
    runs.assert_not_awaited()


@pytest.mark.asyncio
async def test_trigger_without_key_keeps_legacy_behavior():
    service = _service()
    service.scheduler.get_job = MagicMock(return_value=None)

    await service.trigger_task(42)

    kwargs = service.scheduler.add_job.call_args.kwargs
    assert kwargs["id"].startswith("42_manual_")
    assert kwargs["kwargs"] == {"task_id": 42}
