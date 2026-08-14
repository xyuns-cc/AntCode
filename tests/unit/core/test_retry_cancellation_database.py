from datetime import UTC, datetime

import pytest
import pytest_asyncio
from antcode_core.application.services.scheduler.retry_cancellation_service import cancel_retry_intent
from antcode_core.application.services.scheduler.retry_pending_query import (
    list_durable_pending_retries,
)
from antcode_core.domain.models.enums import TaskStatus
from antcode_core.domain.models.task_run import TaskRun
from tortoise import Tortoise

_EXPECTED_ROWS = 3
_USER_ID = 7


@pytest_asyncio.fixture
async def retry_database():
    await Tortoise.init(
        db_url="sqlite://:memory:",
        modules={"models": ["antcode_core.domain.models.task_run"]},
    )
    await Tortoise.generate_schemas()
    try:
        yield
    finally:
        await Tortoise.close_connections()


@pytest.mark.asyncio
async def test_cancel_terminal_retry_preserves_failure_and_clears_intent(retry_database) -> None:
    execution = await TaskRun.create(
        task_id=1,
        run_id="failed-source",
        status=TaskStatus.FAILED,
        error_message="original failure",
        next_retry_at=datetime.now(UTC),
        result_data={"output": {"rows": _EXPECTED_ROWS}, "retry_intent": {"retry_count": 1}},
    )

    await cancel_retry_intent(execution.run_id, user_id=_USER_ID)

    await execution.refresh_from_db()
    assert execution.status == TaskStatus.FAILED
    assert execution.error_message == "original failure"
    assert execution.next_retry_at is None
    assert execution.result_data["output"] == {"rows": _EXPECTED_ROWS}
    assert "retry_intent" not in execution.result_data
    assert execution.result_data["retry_cancellation"]["cancelled_by_user_id"] == _USER_ID


@pytest.mark.asyncio
async def test_cancel_pending_retry_sets_cancelled_without_overwriting_diagnostic(retry_database) -> None:
    execution = await TaskRun.create(
        task_id=1,
        run_id="pending-source",
        status=TaskStatus.PENDING,
        error_message="queued diagnostic",
        next_retry_at=datetime.now(UTC),
    )

    await cancel_retry_intent(execution.run_id, user_id=9)

    await execution.refresh_from_db()
    assert execution.status == TaskStatus.CANCELLED
    assert execution.error_message == "queued diagnostic"
    assert execution.end_time is not None


@pytest.mark.asyncio
async def test_retry_cancellation_is_idempotent_after_redis_cleanup_failure(retry_database) -> None:
    execution = await TaskRun.create(
        task_id=1,
        run_id="repeat-cancel",
        status=TaskStatus.FAILED,
        next_retry_at=datetime.now(UTC),
    )

    await cancel_retry_intent(execution.run_id, user_id=_USER_ID)
    await cancel_retry_intent(execution.run_id, user_id=9)

    await execution.refresh_from_db()
    assert execution.result_data["retry_cancellation"]["cancelled_by_user_id"] == _USER_ID


@pytest.mark.asyncio
async def test_pending_retry_query_uses_postgres_durable_intents(retry_database) -> None:
    retry_time = datetime.now(UTC)
    await TaskRun.create(
        task_id=3,
        run_id="durable-pending",
        status=TaskStatus.FAILED,
        retry_count=2,
        next_retry_at=retry_time,
    )
    await TaskRun.create(
        task_id=3,
        run_id="already-consumed",
        status=TaskStatus.FAILED,
        retry_count=1,
        next_retry_at=None,
    )

    pending = await list_durable_pending_retries()

    assert pending == [
        {
            "task_id": 3,
            "run_id": "durable-pending",
            "retry_time": retry_time.isoformat(),
            "retry_count": 2,
        }
    ]
