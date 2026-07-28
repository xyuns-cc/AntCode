from datetime import UTC, datetime

import pytest
import pytest_asyncio
from antcode_core.application.services.scheduler.retry_configuration_service import apply_retry_configuration
from antcode_core.domain.models.enums import ScheduleType, TaskStatus, TaskType
from antcode_core.domain.models.task import Task
from antcode_core.domain.models.task_run import TaskRun
from tortoise import Tortoise


@pytest_asyncio.fixture
async def retry_database():
    await Tortoise.init(
        db_url="sqlite://:memory:",
        modules={"models": ["antcode_core.domain.models.task", "antcode_core.domain.models.task_run"]},
    )
    await Tortoise.generate_schemas()
    try:
        yield
    finally:
        await Tortoise.close_connections()


@pytest.mark.asyncio
async def test_lowering_retry_limit_cancels_excess_intents_in_same_transaction(retry_database) -> None:
    task = await Task.create(
        name="retry-config-task",
        project_id=1,
        task_type=TaskType.CODE,
        schedule_type=ScheduleType.ONCE,
        user_id=7,
        retry_count=3,
    )
    source = await TaskRun.create(
        task_id=task.id,
        run_id="source-run",
        status=TaskStatus.FAILED,
        retry_count=2,
        error_message="original failure",
        next_retry_at=datetime.now(UTC),
        result_data={"retry_intent": {"retry_count": 2}},
    )

    cancelled = await apply_retry_configuration(task.id, {"retry_count": 1}, user_id=7)

    await task.refresh_from_db()
    await source.refresh_from_db()
    assert cancelled == ["source-run"]
    assert task.retry_count == 1
    assert source.next_retry_at is None
    assert source.error_message == "original failure"
    assert "retry_intent" not in source.result_data


@pytest.mark.asyncio
async def test_increasing_retry_limit_preserves_existing_intent(retry_database) -> None:
    task = await Task.create(
        name="retry-config-increase",
        project_id=1,
        task_type=TaskType.CODE,
        schedule_type=ScheduleType.ONCE,
        user_id=7,
        retry_count=1,
    )
    source = await TaskRun.create(
        task_id=task.id,
        run_id="source-run-increase",
        status=TaskStatus.FAILED,
        retry_count=1,
        next_retry_at=datetime.now(UTC),
    )

    cancelled = await apply_retry_configuration(task.id, {"retry_count": 3}, user_id=7)

    await source.refresh_from_db()
    assert cancelled == []
    assert source.next_retry_at is not None
