"""取消已下发、但持有该 run 的 Worker 代际已消失时的收敛回归。

原缺陷：``record_cancel_request`` 之后所有失败 reaper 都以
``cancel_requested_at is None`` 为前置条件，Worker 换代/消失后没有任何一方
负责把 run 收敛，run 永久非终态并让 ``quiesce_worker_for_delete`` 恒 409。

这里走真 sqlite 往返 + 真 ``execution_status_service``：只有 lease 读取
（Redis）被替身，判死判据与终态写入都是生产代码本身。
"""

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from antcode_core.domain.models.enums import (
    DispatchStatus,
    RuntimeStatus,
    ScheduleType,
    TaskStatus,
    TaskType,
)
from antcode_core.domain.models.scheduler_authority import SchedulerAuthority
from antcode_core.domain.models.task import Task
from antcode_core.domain.models.task_run import TASK_ID_ABSENT, TaskRun
from antcode_master.control import cancel_settlement
from antcode_master.control.cancel_settlement import (
    ABANDONED_CANCEL_ERROR,
    settle_abandoned_cancellations,
)
from tortoise import Tortoise

TOKEN = 31
WORKER_ID = 4
DEAD_GENERATION = "lease-old"
LIVE_GENERATION = "lease-new"


@pytest_asyncio.fixture
async def cancel_database():
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


def _fake_lease_reader(alive: dict[int, str]):
    async def _read(worker_internal_ids):
        return {wid: lease for wid, lease in alive.items() if wid in worker_internal_ids}

    return _read


async def _cancelled_batch_run(run_id: str, **overrides) -> TaskRun:
    """批次 run：``task_id`` 恒为哨兵，``scheduled_tasks`` 里没有对应行。"""
    values = {
        "run_id": run_id,
        "task_id": TASK_ID_ABSENT,
        "status": TaskStatus.QUEUED,
        "dispatch_status": DispatchStatus.DISPATCHED,
        "runtime_status": None,
        "worker_id": WORKER_ID,
        "lease_id": DEAD_GENERATION,
        "cancel_requested_at": datetime.now(UTC),
        "result_data": {"crawl_batch_id": "batch-1"},
    }
    values.update(overrides)
    return await TaskRun.create(**values)


@pytest.mark.asyncio
async def test_dead_generation_cancel_request_is_settled_cancelled(cancel_database, monkeypatch) -> None:
    run = await _cancelled_batch_run("batch-abandoned")
    monkeypatch.setattr(
        cancel_settlement,
        "load_alive_lease_by_worker",
        _fake_lease_reader({WORKER_ID: LIVE_GENERATION}),
    )

    await settle_abandoned_cancellations(TOKEN)

    persisted = await TaskRun.get(id=run.id)
    assert persisted.status == TaskStatus.CANCELLED
    assert persisted.runtime_status == RuntimeStatus.CANCELLED
    assert persisted.end_time is not None
    assert persisted.error_message == ABANDONED_CANCEL_ERROR


@pytest.mark.asyncio
async def test_live_generation_cancel_request_is_left_blocking(cancel_database, monkeypatch) -> None:
    """fail-closed：Worker 仍持有该代际时 run 保持非终态，删除守卫必须继续拦。"""
    run = await _cancelled_batch_run("batch-still-owned", lease_id=LIVE_GENERATION)
    monkeypatch.setattr(
        cancel_settlement,
        "load_alive_lease_by_worker",
        _fake_lease_reader({WORKER_ID: LIVE_GENERATION}),
    )

    await settle_abandoned_cancellations(TOKEN)

    persisted = await TaskRun.get(id=run.id)
    assert persisted.status == TaskStatus.QUEUED
    assert persisted.runtime_status is None
    assert persisted.end_time is None


@pytest.mark.asyncio
async def test_run_without_cancel_request_is_out_of_scope(cancel_database, monkeypatch) -> None:
    """没有取消请求的 run 归失败 reaper 管，本模块不得抢它的终态归属。"""
    run = await _cancelled_batch_run("batch-not-cancelled", cancel_requested_at=None)
    monkeypatch.setattr(
        cancel_settlement,
        "load_alive_lease_by_worker",
        _fake_lease_reader({}),
    )

    await settle_abandoned_cancellations(TOKEN)

    persisted = await TaskRun.get(id=run.id)
    assert persisted.status == TaskStatus.QUEUED
    assert persisted.runtime_status is None


@pytest.mark.asyncio
async def test_missing_liveness_evidence_settles_nothing(cancel_database, monkeypatch) -> None:
    """Lease 存储故障时不得判死——缺证据即收敛会误杀存活 Worker 上的执行。"""
    run = await _cancelled_batch_run("batch-no-evidence")

    async def _unavailable(_worker_internal_ids):
        raise RuntimeError("redis down")

    monkeypatch.setattr(cancel_settlement, "load_alive_lease_by_worker", _unavailable)

    with pytest.raises(RuntimeError, match="redis down"):
        await settle_abandoned_cancellations(TOKEN)

    persisted = await TaskRun.get(id=run.id)
    assert persisted.status == TaskStatus.QUEUED
    assert persisted.runtime_status is None


@pytest.mark.asyncio
async def test_task_backed_run_also_settles_and_syncs_task(cancel_database, monkeypatch) -> None:
    """缺陷不是批次专属：/runs/{id}/cancel 与 /tasks/{id}/stop 走同一条契约。"""
    task = await Task.create(
        name="abandoned-cancel",
        project_id=1,
        task_type=TaskType.CODE,
        schedule_type=ScheduleType.ONCE,
        user_id=1,
        status=TaskStatus.RUNNING,
    )
    run = await _cancelled_batch_run(
        "task-abandoned",
        task_id=task.id,
        status=TaskStatus.RUNNING,
        dispatch_status=DispatchStatus.ACKED,
        runtime_status=RuntimeStatus.RUNNING,
        result_data=None,
    )
    monkeypatch.setattr(
        cancel_settlement,
        "load_alive_lease_by_worker",
        _fake_lease_reader({WORKER_ID: LIVE_GENERATION}),
    )

    await settle_abandoned_cancellations(TOKEN)

    persisted = await TaskRun.get(id=run.id)
    refreshed_task = await Task.get(id=task.id)
    assert persisted.status == TaskStatus.CANCELLED
    assert refreshed_task.status == TaskStatus.CANCELLED
