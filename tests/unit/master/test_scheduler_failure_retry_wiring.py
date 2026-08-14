"""Scheduler dispatch failures persist retry intent before Redis delivery."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from antcode_core.domain.models.enums import DispatchStatus, ScheduleType, TaskStatus, TaskType
from antcode_core.domain.models.scheduler_authority import SchedulerAuthority
from antcode_core.domain.models.task import Task
from antcode_core.domain.models.task_run import TaskRun
from antcode_master.control import retry_dispatch_recovery, scheduler_failure_wiring, scheduler_loop
from antcode_master.control.retry_intent_delivery import RetryIntentDeliveryError
from tortoise import Tortoise

TOKEN = 21


@pytest_asyncio.fixture
async def scheduler_database():
    await Tortoise.init(
        db_url="sqlite://:memory:",
        modules={
            "models": [
                "antcode_core.domain.models.scheduler_authority",
                "antcode_core.domain.models.task",
                "antcode_core.domain.models.task_run",
            ]
        },
    )
    await Tortoise.generate_schemas()
    await SchedulerAuthority.create(name="master", fencing_token=TOKEN, activated_at=datetime.now(UTC))
    try:
        yield
    finally:
        await Tortoise.close_connections()


async def _task() -> Task:
    return await Task.create(
        name=f"task-{datetime.now(UTC).timestamp()}",
        project_id=1,
        task_type=TaskType.CODE,
        schedule_type=ScheduleType.ONCE,
        user_id=1,
    )


async def _run(task: Task, run_id: str, dispatch_status: DispatchStatus) -> TaskRun:
    status = TaskStatus.QUEUED if dispatch_status == DispatchStatus.PENDING else TaskStatus.DISPATCHING
    return await TaskRun.create(
        task_id=task.id,
        run_id=run_id,
        status=status,
        dispatch_status=dispatch_status,
        scheduler_fencing_token=TOKEN,
    )


def _service(result) -> scheduler_loop.SchedulerService:
    service = scheduler_loop.SchedulerService()
    service._log_execution = AsyncMock()
    service._dispatch_and_run = AsyncMock(side_effect=result if isinstance(result, Exception) else None)
    if not isinstance(result, Exception):
        service._dispatch_and_run.return_value = result
    service._finalize_stats = AsyncMock()
    return service


@pytest.mark.asyncio
async def test_dispatch_failure_atomically_persists_retry_intent(scheduler_database, monkeypatch) -> None:
    task = await _task()
    execution = await _run(task, "run-dispatch-failed", DispatchStatus.DISPATCHING)
    service = _service({"success": False, "error": "queue unavailable"})
    deliver = AsyncMock()
    monkeypatch.setattr(scheduler_failure_wiring, "deliver_retry_intent", deliver)

    await _execute(service, task, execution)

    persisted = await TaskRun.get(id=execution.id)
    assert persisted.status == TaskStatus.FAILED
    assert persisted.dispatch_status == DispatchStatus.FAILED
    assert persisted.next_retry_at is not None
    assert persisted.result_data["error"] == "queue unavailable"
    assert persisted.result_data["retry_intent"]["source_run_id"] == execution.run_id
    deliver.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        (TimeoutError(), DispatchStatus.TIMEOUT, TaskStatus.TIMEOUT),
        (RuntimeError("transport failed"), DispatchStatus.FAILED, TaskStatus.FAILED),
    ],
)
async def test_unexpected_failure_creates_durable_retry(
    scheduler_database,
    monkeypatch,
    case,
) -> None:
    failure, expected_dispatch, expected_status = case
    task = await _task()
    execution = await _run(task, f"run-{expected_dispatch.value}", DispatchStatus.PENDING)
    service = _service(failure)
    deliver = AsyncMock()
    monkeypatch.setattr(scheduler_failure_wiring, "deliver_retry_intent", deliver)

    await _execute(service, task, execution)

    persisted = await TaskRun.get(id=execution.id)
    assert persisted.dispatch_status == expected_dispatch
    assert persisted.status == expected_status
    assert persisted.next_retry_at is not None
    deliver.assert_awaited_once()


@pytest.mark.asyncio
async def test_delivery_failure_surfaces_but_keeps_durable_intent(scheduler_database, monkeypatch) -> None:
    task = await _task()
    execution = await _run(task, "run-delivery-failed", DispatchStatus.DISPATCHING)
    service = _service({"success": False, "error": "dispatch failed"})
    monkeypatch.setattr(
        scheduler_failure_wiring,
        "deliver_retry_intent",
        AsyncMock(side_effect=RetryIntentDeliveryError("redis unavailable")),
    )

    with pytest.raises(RetryIntentDeliveryError, match="redis unavailable"):
        await _execute(service, task, execution)

    persisted = await TaskRun.get(id=execution.id)
    assert persisted.status == TaskStatus.FAILED
    assert persisted.next_retry_at is not None
    assert service._finalize_stats.await_args.kwargs["task"] is None


async def _execute(service, task: Task, execution: TaskRun) -> None:
    await retry_dispatch_recovery.run_prepared_execution(
        service,
        task.id,
        (task, object(), object(), execution, datetime.now(UTC)),
    )
