from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.scheduler.scheduler_service import SchedulerService
from antcode_core.application.services.scheduler.trigger_identity import (
    scheduled_run_id,
    trigger_run_id,
)
from antcode_core.domain.models.enums import ScheduleType
from antcode_core.domain.models.task import Task

CREATED_AT = datetime(2026, 8, 17, 3, 4, 5, tzinfo=UTC)


def _task(schedule_type: ScheduleType) -> SimpleNamespace:
    return SimpleNamespace(
        id=42,
        schedule_type=schedule_type,
        scheduled_time=None,
        created_at=CREATED_AT,
    )


def test_trigger_run_id_is_stable_and_key_scoped() -> None:
    assert trigger_run_id(42, "outbox-1") == trigger_run_id("42", "outbox-1")
    assert trigger_run_id(42, "outbox-1") != trigger_run_id(42, "outbox-2")


@pytest.mark.asyncio
async def test_control_plane_trigger_returns_outbox_derived_run_id(monkeypatch) -> None:
    service = SchedulerService()
    publish = AsyncMock(return_value=SimpleNamespace(public_id="outbox-1"))
    monkeypatch.setattr(service, "_publish_event", publish)
    monkeypatch.setattr(
        Task,
        "get_or_none",
        AsyncMock(return_value=_task(ScheduleType.CRON)),
    )

    run_id = await service.trigger_task(42)

    assert run_id == trigger_run_id(42, "outbox-1")
    publish.assert_awaited_once_with("task_trigger", 42)


@pytest.mark.asyncio
async def test_control_plane_trigger_of_one_time_task_returns_schedule_slot_run_id(monkeypatch) -> None:
    """一次性任务的手动触发必须返回调度槽位身份，否则前端订阅的是不存在的 run。"""
    service = SchedulerService()
    task = _task(ScheduleType.ONCE)
    monkeypatch.setattr(service, "_publish_event", AsyncMock(return_value=SimpleNamespace(public_id="outbox-1")))
    monkeypatch.setattr(Task, "get_or_none", AsyncMock(return_value=task))

    run_id = await service.trigger_task(42)

    assert run_id == scheduled_run_id(task)
    assert run_id != trigger_run_id(42, "outbox-1")


@pytest.mark.asyncio
async def test_control_plane_trigger_rejects_missing_task(monkeypatch) -> None:
    service = SchedulerService()
    publish = AsyncMock()
    monkeypatch.setattr(service, "_publish_event", publish)
    monkeypatch.setattr(Task, "get_or_none", AsyncMock(return_value=None))

    with pytest.raises(ValueError, match="触发目标任务不存在"):
        await service.trigger_task(42)
    publish.assert_not_awaited()
