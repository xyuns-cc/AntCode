from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from antcode_core.domain.models.enums import DispatchStatus, RuntimeStatus, ScheduleType, TaskStatus, TaskType
from antcode_core.domain.models.scheduler_authority import SchedulerAuthority
from antcode_core.domain.models.task import Task
from antcode_core.domain.models.task_run import TASK_ID_ABSENT, TaskRun
from antcode_master.control.failure_settlement import (
    ANY_OWNERSHIP,
    FailurePlane,
    FailureSettlementRequest,
    settle_failure,
)
from antcode_master.control.scheduler_authority import SchedulerAuthorityLost
from tortoise import Tortoise

TOKEN = 17
RETRY_DELAY_SECONDS = 30
EXECUTION_DURATION_SECONDS = 5
EXPECTED_WORKER_ID = 7
OTHER_WORKER_ID = 8


@pytest_asyncio.fixture
async def settlement_database():
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


async def _task(*, retries: int = 2, active: bool = True) -> Task:
    return await Task.create(
        name=f"settlement-{datetime.now(UTC).timestamp()}",
        project_id=1,
        task_type=TaskType.CODE,
        schedule_type=ScheduleType.ONCE,
        user_id=1,
        retry_count=retries,
        retry_delay=RETRY_DELAY_SECONDS,
        status=TaskStatus.RUNNING,
        is_active=active,
    )


async def _run(task: Task, **overrides) -> TaskRun:
    values = {
        "task_id": task.id,
        "run_id": f"run-{datetime.now(UTC).timestamp()}",
        "scheduler_fencing_token": TOKEN,
        "dispatch_status": DispatchStatus.DISPATCHING,
        "runtime_status": None,
        "status": TaskStatus.DISPATCHING,
        "retry_count": 0,
    }
    values.update(overrides)
    return await TaskRun.create(**values)


def _request(run: TaskRun, *, plane=FailurePlane.DISPATCH, terminal=DispatchStatus.FAILED, **overrides):
    values = {
        "run_id": run.run_id,
        "authority_token": TOKEN,
        "expected_scheduler_fencing_token": run.scheduler_fencing_token,
        "plane": plane,
        "terminal_status": terminal,
        "error_message": "dispatch failed",
        "status_at": datetime.now(UTC),
        "expected_dispatch_statuses": frozenset({run.dispatch_status}),
        "expected_runtime_statuses": frozenset({run.runtime_status}),
        "expected_worker_id": ANY_OWNERSHIP,
        "expected_lease_id": ANY_OWNERSHIP,
    }
    values.update(overrides)
    return FailureSettlementRequest(**values)


@pytest.mark.asyncio
async def test_failed_status_and_retry_intent_commit_together(settlement_database) -> None:
    task = await _task()
    status_at = datetime.now(UTC)
    run = await _run(
        task,
        start_time=status_at - timedelta(seconds=EXECUTION_DURATION_SECONDS),
        result_data={"created": True},
    )

    result = await settle_failure(_request(run, status_at=status_at, result_update={"failure_plane": "dispatch"}))

    persisted = await TaskRun.get(id=run.id)
    refreshed_task = await Task.get(id=task.id)
    assert result.settled is True
    assert result.intent is not None
    assert result.intent.retry_time == status_at + timedelta(seconds=RETRY_DELAY_SECONDS)
    assert persisted.dispatch_status == DispatchStatus.FAILED
    assert persisted.status == TaskStatus.FAILED
    assert persisted.end_time == status_at
    assert persisted.duration_seconds == EXECUTION_DURATION_SECONDS
    assert persisted.next_retry_at == result.intent.retry_time
    assert persisted.result_data["created"] is True
    assert persisted.result_data["failure_plane"] == "dispatch"
    assert persisted.result_data["retry_intent"]["source_run_id"] == run.run_id
    assert refreshed_task.status == TaskStatus.FAILED
    assert refreshed_task.failure_count == 1


@pytest.mark.asyncio
async def test_runtime_timeout_commits_retry_intent(settlement_database) -> None:
    task = await _task()
    run = await _run(
        task,
        dispatch_status=DispatchStatus.ACKED,
        runtime_status=RuntimeStatus.RUNNING,
        status=TaskStatus.RUNNING,
        worker_id=9,
        lease_id="lease-current",
    )
    request = _request(
        run,
        plane=FailurePlane.RUNTIME,
        terminal=RuntimeStatus.TIMEOUT,
        error_message="runtime timeout",
        expected_worker_id=9,
        expected_lease_id="lease-current",
    )

    result = await settle_failure(request)

    persisted = await TaskRun.get(id=run.id)
    assert result.settled is True
    assert result.intent is not None
    assert persisted.runtime_status == RuntimeStatus.TIMEOUT
    assert persisted.status == TaskStatus.TIMEOUT
    assert persisted.next_retry_at == result.intent.retry_time


@pytest.mark.asyncio
async def test_stale_scheduler_epoch_is_rejected(settlement_database) -> None:
    task = await _task()
    run = await _run(task, scheduler_fencing_token=TOKEN - 1)

    with pytest.raises(SchedulerAuthorityLost):
        await settle_failure(_request(run, authority_token=TOKEN - 1))

    assert (await TaskRun.get(id=run.id)).dispatch_status == DispatchStatus.DISPATCHING

    takeover = await settle_failure(_request(run, authority_token=TOKEN, expected_scheduler_fencing_token=TOKEN - 1))
    assert takeover.settled is True


@pytest.mark.asyncio
async def test_current_leader_can_settle_legacy_null_scheduler_token(settlement_database) -> None:
    task = await _task()
    run = await _run(task, scheduler_fencing_token=None)

    result = await settle_failure(_request(run, expected_scheduler_fencing_token=None))

    assert result.settled is True
    assert (await TaskRun.get(id=run.id)).dispatch_status == DispatchStatus.FAILED


@pytest.mark.asyncio
async def test_ownership_mismatch_rejects_but_expected_null_lease_matches(settlement_database) -> None:
    task = await _task()
    run = await _run(task, worker_id=EXPECTED_WORKER_ID, lease_id=None)

    rejected = await settle_failure(_request(run, expected_worker_id=OTHER_WORKER_ID, expected_lease_id=None))
    settled = await settle_failure(_request(run, expected_worker_id=EXPECTED_WORKER_ID, expected_lease_id=None))

    assert rejected.settled is False
    assert rejected.intent is None
    assert settled.settled is True
    assert (await TaskRun.get(id=run.id)).worker_id == EXPECTED_WORKER_ID


@pytest.mark.asyncio
async def test_snapshot_conflict_does_not_write_retry_intent(settlement_database) -> None:
    task = await _task()
    run = await _run(task)
    request = _request(run, expected_dispatch_statuses=frozenset({DispatchStatus.DISPATCHED}))

    result = await settle_failure(request)

    persisted = await TaskRun.get(id=run.id)
    assert result == type(result)(settled=False)
    assert persisted.dispatch_status == DispatchStatus.DISPATCHING
    assert persisted.next_retry_at is None
    assert persisted.result_data is None


@pytest.mark.asyncio
@pytest.mark.parametrize(("retries", "active"), [(0, True), (2, False)])
async def test_retry_ineligible_settles_without_intent(settlement_database, retries: int, active: bool) -> None:
    task = await _task(retries=retries, active=active)
    run = await _run(task)

    result = await settle_failure(_request(run))

    persisted = await TaskRun.get(id=run.id)
    assert result.settled is True
    assert result.intent is None
    assert persisted.next_retry_at is None
    assert persisted.retry_count == 0
    assert "retry_intent" not in persisted.result_data


@pytest.mark.asyncio
async def test_cancel_requested_run_is_not_settled_or_retried(settlement_database) -> None:
    task = await _task()
    cancelled_at = datetime.now(UTC)
    run = await _run(task, cancel_requested_at=cancelled_at)

    result = await settle_failure(_request(run))

    persisted = await TaskRun.get(id=run.id)
    assert result.settled is False
    assert result.intent is None
    assert persisted.dispatch_status == DispatchStatus.DISPATCHING
    assert persisted.next_retry_at is None
    assert persisted.result_data is None


@pytest.mark.asyncio
async def test_task_less_batch_run_is_settled_without_retry_intent(settlement_database) -> None:
    """批次 run 的 ``task_id`` 是 ``TASK_ID_ABSENT`` 哨兵，``scheduled_tasks`` 里没有对应行。

    此前"取不到 Task 就返回未结算"让 no-ack / 超时 / 失租 / 僵尸等全部 reaper 对
    批次 run 永久无效，run 卡非终态并让 Worker 永远删不掉。
    """
    run = await TaskRun.create(
        task_id=TASK_ID_ABSENT,
        run_id="batch-taskless",
        scheduler_fencing_token=TOKEN,
        dispatch_status=DispatchStatus.DISPATCHED,
        runtime_status=None,
        status=TaskStatus.QUEUED,
        retry_count=0,
        result_data={"crawl_batch_id": "batch-1"},
    )

    result = await settle_failure(_request(run))

    persisted = await TaskRun.get(id=run.id)
    assert result.settled is True
    # 无 Task ⇒ 无任务级重试策略，不得伪造带假 task_id 的 RetryIntent。
    assert result.intent is None
    assert persisted.dispatch_status == DispatchStatus.FAILED
    assert persisted.status == TaskStatus.FAILED
    assert persisted.next_retry_at is None
    assert "retry_intent" not in persisted.result_data
    assert persisted.result_data["crawl_batch_id"] == "batch-1"


@pytest.mark.asyncio
async def test_orphan_run_with_real_task_id_is_still_rejected(settlement_database) -> None:
    """真丢了 Task 行的孤儿 run 仍不结算：哨兵豁免不得退化成"任务不存在就当没有"。"""
    task = await _task()
    run = await _run(task, run_id="orphan-run")
    await Task.filter(id=task.id).delete()

    result = await settle_failure(_request(run))

    assert result.settled is False
    assert (await TaskRun.get(id=run.id)).dispatch_status == DispatchStatus.DISPATCHING


@pytest.mark.asyncio
async def test_only_latest_run_overwrites_task_status(settlement_database) -> None:
    task = await _task()
    old_run = await _run(task, run_id="old-run")
    await _run(task, run_id="latest-run", status=TaskStatus.RUNNING)

    result = await settle_failure(_request(old_run))

    refreshed_task = await Task.get(id=task.id)
    assert result.settled is True
    assert refreshed_task.status == TaskStatus.RUNNING
    assert refreshed_task.failure_count == 1
