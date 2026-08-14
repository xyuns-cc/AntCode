from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, Mock

import pytest
from antcode_core.domain.models.enums import ScheduleType
from antcode_core.domain.schemas.task import TaskUpdateRequest

from tests.unit.core.test_scheduler_task_update_concurrency import (
    _configure_locked_task,
    _configure_service,
    scheduler_module,
)

UPDATED_INTERVAL_SECONDS = 30


@pytest.mark.asyncio
async def test_schedule_type_change_revalidates_and_reschedules(monkeypatch) -> None:
    fresh = SimpleNamespace(
        id=4,
        name="task",
        is_active=True,
        schedule_type=ScheduleType.CRON,
        cron_expression="0 0 * * *",
        interval_seconds=None,
        scheduled_time=datetime(2026, 8, 12, tzinfo=UTC),
        next_run_time=datetime(2026, 8, 13, tzinfo=UTC),
        save=AsyncMock(),
    )
    _configure_locked_task(monkeypatch, fresh)
    service = _configure_service(monkeypatch)
    create_trigger = Mock(return_value=object())
    monkeypatch.setattr(service, "_create_trigger", create_trigger)
    monkeypatch.setattr(
        scheduler_module.QueryHelper,
        "get_by_id_or_public_id",
        AsyncMock(return_value=SimpleNamespace(id=4)),
    )

    result = await service.update_task(
        "task-public",
        TaskUpdateRequest(
            schedule_type=ScheduleType.INTERVAL,
            interval_seconds=UPDATED_INTERVAL_SECONDS,
        ),
        user_id=7,
    )

    assert result is fresh
    assert fresh.schedule_type == ScheduleType.INTERVAL
    assert fresh.cron_expression is None
    assert fresh.interval_seconds == UPDATED_INTERVAL_SECONDS
    assert fresh.scheduled_time is None
    assert fresh.next_run_time is None
    create_trigger.assert_called_once_with(fresh)
    fresh.save.assert_awaited_once_with(
        using_db=ANY,
        update_fields=[
            "schedule_type",
            "interval_seconds",
            "cron_expression",
            "scheduled_time",
            "next_run_time",
        ],
    )
    # 重建 Job 由 Master 消费 outbox 后完成，core 侧只负责投递事件。
    service._publish_event.assert_awaited_once_with("task_changed", 4, connection=ANY)


@pytest.mark.asyncio
async def test_schedule_type_change_requires_new_target_field(monkeypatch) -> None:
    stale_time = datetime(2026, 8, 12, tzinfo=UTC)
    fresh = SimpleNamespace(
        id=5,
        schedule_type=ScheduleType.CRON,
        cron_expression="0 0 * * *",
        interval_seconds=None,
        scheduled_time=stale_time,
        save=AsyncMock(),
    )
    _configure_locked_task(monkeypatch, fresh)
    service = _configure_service(monkeypatch)
    monkeypatch.setattr(
        scheduler_module.QueryHelper,
        "get_by_id_or_public_id",
        AsyncMock(return_value=SimpleNamespace(id=5)),
    )

    with pytest.raises(ValueError, match="DATE 调度必须提供 scheduled_time"):
        await service.update_task(
            "task-public",
            TaskUpdateRequest(schedule_type=ScheduleType.DATE),
            user_id=7,
        )

    assert fresh.schedule_type == ScheduleType.CRON
    assert fresh.scheduled_time == stale_time
    fresh.save.assert_not_awaited()
    service._publish_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_schedule_update_rejects_non_target_trigger_field(monkeypatch) -> None:
    fresh = SimpleNamespace(
        id=6,
        schedule_type=ScheduleType.CRON,
        cron_expression="0 0 * * *",
        interval_seconds=None,
        scheduled_time=None,
        save=AsyncMock(),
    )
    _configure_locked_task(monkeypatch, fresh)
    service = _configure_service(monkeypatch)
    monkeypatch.setattr(
        scheduler_module.QueryHelper,
        "get_by_id_or_public_id",
        AsyncMock(return_value=SimpleNamespace(id=6)),
    )

    with pytest.raises(ValueError, match="INTERVAL 调度不接受字段: cron_expression"):
        await service.update_task(
            "task-public",
            TaskUpdateRequest(
                schedule_type=ScheduleType.INTERVAL,
                interval_seconds=UPDATED_INTERVAL_SECONDS,
                cron_expression="0 1 * * *",
            ),
            user_id=7,
        )

    assert fresh.schedule_type == ScheduleType.CRON
    fresh.save.assert_not_awaited()
    service._publish_event.assert_not_awaited()
