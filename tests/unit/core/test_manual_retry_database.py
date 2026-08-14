from datetime import UTC, datetime

import pytest
import pytest_asyncio
from antcode_core.application.services.scheduler.manual_retry_outbox import get_manual_retry_event
from antcode_core.application.services.scheduler.manual_retry_service import execute_manual_retry
from antcode_core.application.services.scheduler.outbox_service import scheduler_outbox_service
from antcode_core.domain.models.enums import ScheduleType, TaskStatus, TaskType
from antcode_core.domain.models.scheduler_outbox import SchedulerOutbox
from antcode_core.domain.models.task import Task
from antcode_core.domain.models.task_run import TaskRun
from tortoise import Tortoise


@pytest_asyncio.fixture
async def retry_database():
    await Tortoise.init(
        db_url="sqlite://:memory:",
        modules={
            "models": [
                "antcode_core.domain.models.task",
                "antcode_core.domain.models.task_run",
                "antcode_core.domain.models.scheduler_outbox",
            ]
        },
    )
    await Tortoise.generate_schemas()
    try:
        yield
    finally:
        await Tortoise.close_connections()


@pytest.mark.asyncio
async def test_manual_retry_preserves_terminal_run_and_commits_outbox(retry_database):
    task = await Task.create(
        name="manual-retry-task",
        project_id=1,
        task_type=TaskType.CODE,
        schedule_type=ScheduleType.ONCE,
        user_id=7,
        status=TaskStatus.FAILED,
    )
    source = await TaskRun.create(
        task_id=task.id,
        run_id="source-run",
        status=TaskStatus.FAILED,
        retry_count=1,
        next_retry_at=datetime.now(UTC),
        result_data={"output": {"rows": 3}},
    )

    async def cancel_pending(run_id: str) -> int:
        assert run_id == source.run_id
        return 1

    result = await execute_manual_retry(
        source.run_id,
        7,
        cancel_pending=cancel_pending,
        enqueue_event=scheduler_outbox_service.enqueue,
        get_event=get_manual_retry_event,
    )

    await source.refresh_from_db()
    event = await SchedulerOutbox.get()
    assert result["auto_intent_consumed"] is True
    assert source.status == TaskStatus.FAILED
    assert source.retry_count == 1
    assert source.result_data == {"output": {"rows": 3}}
    assert source.next_retry_at is None
    assert event.event_type == "task_trigger"
    assert event.aggregate_type == "manual_retry"
    assert event.aggregate_id == source.run_id
    assert event.payload == {"task_id": str(task.id), "manual_retry_source_run_id": source.run_id}


@pytest.mark.asyncio
async def test_outbox_failure_restores_durable_automatic_intent(retry_database):
    task = await Task.create(
        name="manual-retry-rollback-task",
        project_id=1,
        task_type=TaskType.CODE,
        schedule_type=ScheduleType.ONCE,
        user_id=7,
        status=TaskStatus.FAILED,
    )
    retry_time = datetime.now(UTC)
    source = await TaskRun.create(
        task_id=task.id,
        run_id="rollback-source-run",
        status=TaskStatus.FAILED,
        next_retry_at=retry_time,
    )

    async def cancel_pending(_run_id: str) -> int:
        return 1

    async def fail_enqueue(**_kwargs):
        raise RuntimeError("outbox unavailable")

    with pytest.raises(RuntimeError, match="outbox unavailable"):
        await execute_manual_retry(
            source.run_id,
            7,
            cancel_pending=cancel_pending,
            enqueue_event=fail_enqueue,
            get_event=get_manual_retry_event,
        )

    await source.refresh_from_db()
    assert source.next_retry_at == retry_time
    assert await SchedulerOutbox.all().count() == 0


@pytest.mark.asyncio
async def test_repeated_manual_retry_reuses_one_outbox(retry_database):
    task = await Task.create(
        name="manual-retry-idempotent-task",
        project_id=1,
        task_type=TaskType.CODE,
        schedule_type=ScheduleType.ONCE,
        user_id=7,
        status=TaskStatus.FAILED,
    )
    source = await TaskRun.create(
        task_id=task.id,
        run_id="idempotent-source-run",
        status=TaskStatus.FAILED,
        next_retry_at=datetime.now(UTC),
    )

    async def cancel_pending(_run_id: str) -> int:
        return 0

    async def request() -> dict:
        return await execute_manual_retry(
            source.run_id,
            7,
            cancel_pending=cancel_pending,
            enqueue_event=scheduler_outbox_service.enqueue,
            get_event=get_manual_retry_event,
        )

    first = await request()
    second = await request()

    assert first["already_requested"] is False
    assert second["already_requested"] is True
    assert await SchedulerOutbox.all().count() == 1
