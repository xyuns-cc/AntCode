"""Durable one-time scheduling and Master generation fencing."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from antcode_core.application.services.lease_fenced_ready_publish import (
    SchedulerDispatchFenceError,
)
from antcode_core.domain.models.enums import DispatchStatus, ScheduleType, TaskStatus, TaskType
from antcode_core.domain.models.scheduler_authority import SchedulerAuthority
from antcode_core.domain.models.task import Task
from antcode_core.domain.models.task_run import TaskRun
from antcode_master.control import durable_schedule, retry_dispatch_recovery, scheduler_loop
from antcode_master.control.durable_schedule import load_due_tasks_page, scheduled_run_id
from antcode_master.control.scheduler_authority import (
    SchedulerAuthorityLost,
    claim_dispatch,
    execute_with_scheduler_authority,
    reset_unpublished_dispatch,
    take_over_pre_dispatch_run,
)
from tortoise import Tortoise

FENCED_TOKEN = 21
RETRY_LIMIT = 3
TAKEOVER_TOKEN = 8


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
    try:
        yield
    finally:
        await Tortoise.close_connections()


async def _task(*, schedule_type=ScheduleType.DATE, scheduled_time=None, active=True) -> Task:
    return await Task.create(
        name=f"task-{datetime.now(UTC).timestamp()}",
        project_id=1,
        task_type=TaskType.CODE,
        schedule_type=schedule_type,
        scheduled_time=scheduled_time,
        is_active=active,
        user_id=1,
    )


async def _authority(token: int) -> None:
    await SchedulerAuthority.create(
        name="master",
        fencing_token=token,
        activated_at=datetime.now(UTC),
    )


async def _allow_authority(_token, function, **kwargs):
    return await function(**kwargs)


@pytest.mark.asyncio
async def test_due_scan_recovers_dates_far_beyond_misfire_grace(scheduler_database) -> None:
    old_date = await _task(scheduled_time=datetime.now(UTC) - timedelta(hours=2))
    immediate_once = await _task(schedule_type=ScheduleType.ONCE)
    await _task(scheduled_time=datetime.now(UTC) + timedelta(hours=2))
    await _task(scheduled_time=datetime.now(UTC) - timedelta(hours=2), active=False)

    due = await load_due_tasks_page(datetime.now(UTC))

    assert {task.id for task in due} == {old_date.id, immediate_once.id}
    assert scheduled_run_id(old_date) == scheduled_run_id(old_date)


@pytest.mark.asyncio
async def test_one_time_schedule_closes_only_after_its_run_is_settled(scheduler_database, monkeypatch) -> None:
    await _authority(2)
    task = await _task(scheduled_time=datetime.now(UTC) - timedelta(minutes=5))
    run_id = scheduled_run_id(task)
    service = scheduler_loop.SchedulerService()

    async def create_run(*_args, **_kwargs):
        await TaskRun.create(
            task_id=task.id,
            run_id=run_id,
            status=TaskStatus.QUEUED,
            dispatch_status=DispatchStatus.PENDING,
            scheduler_fencing_token=2,
        )
        return run_id

    monkeypatch.setattr(service, "_execute_task", create_run)
    await service._durable_schedule.execute_task(task, 2)
    # run 还在飞行中：槽位不能关闭，否则该 run 失败后的重试会被判目标无效丢弃。
    assert (await Task.get(id=task.id)).is_active is True

    await TaskRun.filter(run_id=run_id).update(status=TaskStatus.SUCCESS, dispatch_status=DispatchStatus.DISPATCHED)
    await service._durable_schedule.execute_task(await Task.get(id=task.id), 2)

    persisted = await Task.get(id=task.id)
    assert persisted.is_active is False
    assert persisted.next_run_time is None
    assert await TaskRun.filter(run_id=run_id).count() == 1
    assert await load_due_tasks_page(datetime.now(UTC)) == []


@pytest.mark.asyncio
async def test_busy_due_task_stays_active_for_next_database_sweep(scheduler_database, monkeypatch) -> None:
    await _authority(3)
    task = await _task(scheduled_time=datetime.now(UTC) - timedelta(minutes=5))
    service = scheduler_loop.SchedulerService()
    monkeypatch.setattr(service, "_execute_task", AsyncMock(return_value=None))

    await service._durable_schedule.execute_task(task, 3)

    assert (await Task.get(id=task.id)).is_active is True
    assert await TaskRun.filter(task_id=task.id).count() == 0


@pytest.mark.asyncio
async def test_new_leader_takes_over_committed_pre_dispatch_run(scheduler_database, monkeypatch) -> None:
    await _authority(TAKEOVER_TOKEN)
    task = await _task(scheduled_time=datetime.now(UTC) - timedelta(hours=1))
    run_id = scheduled_run_id(task)
    await TaskRun.create(
        task_id=task.id,
        run_id=run_id,
        status=TaskStatus.QUEUED,
        dispatch_status=DispatchStatus.PENDING,
        scheduler_fencing_token=TAKEOVER_TOKEN - 1,
    )
    resume = AsyncMock(return_value=run_id)
    monkeypatch.setattr(durable_schedule, "resume_existing_run", resume)

    await scheduler_loop.SchedulerService()._durable_schedule.execute_task(task, TAKEOVER_TOKEN)

    run = await TaskRun.get(run_id=run_id)
    assert run.scheduler_fencing_token == TAKEOVER_TOKEN
    assert (await Task.get(id=task.id)).is_active is True
    resume.assert_awaited_once()


@pytest.mark.asyncio
async def test_stale_epoch_cannot_claim_dispatch_after_promotion(scheduler_database) -> None:
    await _authority(12)
    run = await TaskRun.create(
        task_id=1,
        run_id="run-old",
        status=TaskStatus.QUEUED,
        dispatch_status=DispatchStatus.PENDING,
        scheduler_fencing_token=11,
    )

    with pytest.raises(SchedulerAuthorityLost, match="expected=11 current=12"):
        await claim_dispatch(run.run_id, 11, datetime.now(UTC))

    owned = await take_over_pre_dispatch_run(run.run_id, 12)
    assert owned is not None
    assert await claim_dispatch(run.run_id, 12, datetime.now(UTC)) is True


@pytest.mark.asyncio
async def test_new_epoch_recovers_dispatching_run_before_runtime_start(scheduler_database) -> None:
    await _authority(TAKEOVER_TOKEN)
    run = await TaskRun.create(
        task_id=1,
        run_id="run-crashed-before-publish",
        status=TaskStatus.DISPATCHING,
        dispatch_status=DispatchStatus.DISPATCHING,
        scheduler_fencing_token=TAKEOVER_TOKEN - 1,
        worker_id=9,
        lease_id="lease-old",
        lease_gen=4,
    )

    owned = await take_over_pre_dispatch_run(run.run_id, TAKEOVER_TOKEN)

    assert owned is not None
    assert owned.dispatch_status == DispatchStatus.PENDING
    assert owned.status == TaskStatus.QUEUED
    assert owned.worker_id is None
    assert owned.lease_id is None
    assert owned.lease_gen is None


@pytest.mark.asyncio
async def test_current_epoch_cannot_take_over_active_dispatch(scheduler_database) -> None:
    await _authority(TAKEOVER_TOKEN)
    run = await TaskRun.create(
        task_id=1,
        run_id="run-current-dispatch",
        status=TaskStatus.DISPATCHING,
        dispatch_status=DispatchStatus.DISPATCHING,
        scheduler_fencing_token=TAKEOVER_TOKEN,
    )

    assert await take_over_pre_dispatch_run(run.run_id, TAKEOVER_TOKEN) is None
    assert (await TaskRun.get(id=run.id)).dispatch_status == DispatchStatus.DISPATCHING


@pytest.mark.asyncio
async def test_business_write_requires_current_scheduler_authority(scheduler_database) -> None:
    await _authority(TAKEOVER_TOKEN)
    operation = AsyncMock(return_value="written")

    assert await execute_with_scheduler_authority(TAKEOVER_TOKEN, operation, value=1) == "written"
    operation.assert_awaited_once_with(value=1)

    await SchedulerAuthority.filter(name="master").update(fencing_token=TAKEOVER_TOKEN + 1)
    with pytest.raises(SchedulerAuthorityLost, match="current=9"):
        await execute_with_scheduler_authority(TAKEOVER_TOKEN, operation, value=2)
    assert operation.await_count == 1


@pytest.mark.asyncio
async def test_rejected_publish_releases_worker_binding_for_takeover(scheduler_database) -> None:
    run = await TaskRun.create(
        task_id=1,
        run_id="run-rejected",
        status=TaskStatus.DISPATCHING,
        dispatch_status=DispatchStatus.DISPATCHING,
        scheduler_fencing_token=20,
        worker_id=9,
        lease_id="lease-old",
        lease_gen=4,
    )

    assert await reset_unpublished_dispatch(run.run_id, 20) is True

    refreshed = await TaskRun.get(id=run.id)
    assert refreshed.dispatch_status == DispatchStatus.PENDING
    assert refreshed.worker_id is None
    assert refreshed.lease_id is None
    assert refreshed.lease_gen is None


@pytest.mark.asyncio
async def test_publish_fence_is_not_converted_to_business_failure(monkeypatch) -> None:
    service = scheduler_loop.SchedulerService()
    task = SimpleNamespace(id=1, name="fenced-task", retry_count=0)
    execution = SimpleNamespace(run_id="run-fenced", scheduler_fencing_token=FENCED_TOKEN)
    prepared = (task, object(), object(), execution, datetime.now(UTC))
    service._log_execution = AsyncMock()
    service._dispatch_and_run = AsyncMock(side_effect=SchedulerDispatchFenceError("stale"))
    service._finalize_stats = AsyncMock()
    reset = AsyncMock(return_value=True)
    monkeypatch.setattr(retry_dispatch_recovery, "reset_unpublished_dispatch", reset)
    monkeypatch.setattr(retry_dispatch_recovery, "execute_with_scheduler_authority", _allow_authority)

    with pytest.raises(SchedulerAuthorityLost, match="任期已切换"):
        await retry_dispatch_recovery.run_prepared_execution(service, task.id, prepared)

    reset.assert_awaited_once_with("run-fenced", FENCED_TOKEN)
    assert service._finalize_stats.await_args.kwargs["task"] is None


@pytest.mark.asyncio
async def test_stale_authority_is_not_converted_to_business_failure(monkeypatch) -> None:
    service = scheduler_loop.SchedulerService()
    task = SimpleNamespace(id=1, name="stale-task", retry_count=RETRY_LIMIT)
    execution = SimpleNamespace(run_id="run-stale", scheduler_fencing_token=FENCED_TOKEN)
    prepared = (task, object(), object(), execution, datetime.now(UTC))
    service._log_execution = AsyncMock()
    service._dispatch_and_run = AsyncMock(side_effect=SchedulerAuthorityLost("stale"))
    service._finalize_stats = AsyncMock()
    monkeypatch.setattr(retry_dispatch_recovery, "execute_with_scheduler_authority", _allow_authority)

    with pytest.raises(SchedulerAuthorityLost, match="stale"):
        await retry_dispatch_recovery.run_prepared_execution(service, task.id, prepared)

    assert service._finalize_stats.await_args.kwargs["task"] is None
