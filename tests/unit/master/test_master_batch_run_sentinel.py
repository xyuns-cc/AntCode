"""Master 侧对爬取批次 run 哨兵的处理：临时 Worker 清理 + 孤儿告警。

批次 run 的 ``task_id`` 是 ``TASK_ID_ABSENT`` 哨兵，``scheduled_tasks`` 里永远没有对应行。
``_lock_tasks`` 用"锁到的 Task 数 == 传入 task_id 数"判完整性，把哨兵带进去会必然触发
"关联任务缺失"而抛错——持有活跃批次 run 的临时 Worker 因此永远清理不掉、永远删不掉。

这里不 mock ``_lock_tasks``（它正是会炸的那一环），用真表真查跑完整链路。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import pytest_asyncio
from antcode_core.domain.models import Task, TaskRun
from antcode_core.domain.models.enums import DispatchStatus, ScheduleType, TaskStatus, TaskType
from antcode_core.domain.models.task_run import TASK_ID_ABSENT
from antcode_master.control import provisional_worker_cleanup
from tortoise import Tortoise, connections

WORKER_ID = 5
PROJECT_ID = 11
OWNER_ID = 7
SCHEDULED_TASK_ID = 21
ORPHAN_TASK_ID = 999


@pytest_asyncio.fixture
async def cleanup_tables():
    await Tortoise.init(
        db_url="sqlite://:memory:",
        modules={"models": ["antcode_core.domain.models"]},
    )
    await Tortoise.generate_schemas()
    try:
        yield connections.get("default")
    finally:
        await Tortoise.close_connections()


async def _create_run(run_id: str, task_id: int) -> None:
    await TaskRun.create(
        run_id=run_id,
        task_id=task_id,
        status=TaskStatus.RUNNING,
        dispatch_status=DispatchStatus.PENDING,
        worker_id=WORKER_ID,
    )


async def _create_task(task_id: int) -> None:
    await Task.create(
        id=task_id,
        name="计划任务",
        project_id=PROJECT_ID,
        task_type=TaskType.CODE,
        schedule_type=ScheduleType.ONCE,
        user_id=OWNER_ID,
    )


@pytest.mark.asyncio
async def test_batch_run_sentinel_is_not_treated_as_a_task_id(cleanup_tables) -> None:
    """回归: 哨兵混进 task_ids 会让 _lock_tasks 判定"关联任务缺失"。"""
    await _create_run("batch-run", TASK_ID_ABSENT)

    task_ids = await provisional_worker_cleanup._load_active_task_ids(cleanup_tables, WORKER_ID)

    assert task_ids == []


@pytest.mark.asyncio
async def test_lock_tasks_accepts_worker_holding_only_batch_runs(cleanup_tables) -> None:
    """整链路: 只持有批次 run 的临时 Worker 必须能走完加锁这一步。"""
    await _create_run("batch-run", TASK_ID_ABSENT)

    task_ids = await provisional_worker_cleanup._load_active_task_ids(cleanup_tables, WORKER_ID)
    await provisional_worker_cleanup._lock_tasks(cleanup_tables, task_ids)


@pytest.mark.asyncio
async def test_mixed_worker_still_locks_the_real_task(cleanup_tables) -> None:
    await _create_task(SCHEDULED_TASK_ID)
    await _create_run("batch-run", TASK_ID_ABSENT)
    await _create_run("task-run", SCHEDULED_TASK_ID)

    task_ids = await provisional_worker_cleanup._load_active_task_ids(cleanup_tables, WORKER_ID)

    assert task_ids == [SCHEDULED_TASK_ID]
    await provisional_worker_cleanup._lock_tasks(cleanup_tables, task_ids)


@pytest.mark.asyncio
async def test_true_orphan_run_still_aborts_cleanup(cleanup_tables) -> None:
    """哨兵豁免不得退化成"任务不存在就放过"：真丢 Task 行的孤儿 run 仍须拒绝静默解绑。"""
    await _create_run("orphan-run", ORPHAN_TASK_ID)

    task_ids = await provisional_worker_cleanup._load_active_task_ids(cleanup_tables, WORKER_ID)

    assert task_ids == [ORPHAN_TASK_ID]
    with pytest.raises(RuntimeError, match="关联任务缺失"):
        await provisional_worker_cleanup._lock_tasks(cleanup_tables, task_ids)


@pytest.mark.asyncio
async def test_sync_tasks_skips_sentinel_instead_of_counting_a_pseudo_task(cleanup_tables, monkeypatch) -> None:
    """批次 run 没有 Task 载体：按哨兵去统计会把所有批次 run 当成同一个伪任务。

    断言"统计被哪些 task_id 调用"而不是"Task 表没有 id=0 的行"—— 后者无论修没修都成立
    （``UPDATE ... WHERE id=0`` 命中 0 行），是个恒真的假绿断言。
    """
    await _create_task(SCHEDULED_TASK_ID)
    await _create_run("batch-run", TASK_ID_ABSENT)
    await _create_run("task-run", SCHEDULED_TASK_ID)
    runs = await TaskRun.filter(worker_id=WORKER_ID).all()
    counted: list[int] = []

    async def _record(_connection, task_id):
        counted.append(task_id)
        return {}

    monkeypatch.setattr(provisional_worker_cleanup, "task_run_outcome_counts", _record)

    await provisional_worker_cleanup._sync_tasks(cleanup_tables, runs)

    assert counted == [SCHEDULED_TASK_ID]


@pytest.mark.asyncio
async def test_orphan_check_excludes_the_batch_run_sentinel(monkeypatch):
    """孤儿告警把每条批次 run 都算成孤儿，会让它永久为真、失去指示意义。

    只能断到 SQL 契约：这条语句是 Postgres 方言（``$1`` 占位符），跑不到 sqlite 上。
    """
    from antcode_master.ingester.artifact_cleanup_loop import ArtifactCleanupLoop

    captured = {}

    class RecordingConnection:
        async def execute_query(self, sql, params=None):
            captured["sql"] = sql
            captured["params"] = params
            return None, [{"n": 0}]

    monkeypatch.setattr("tortoise.connections", SimpleNamespace(get=lambda _name: RecordingConnection()))

    await ArtifactCleanupLoop()._check_orphans()

    assert "te.task_id <> $1" in captured["sql"]
    assert captured["params"] == [TASK_ID_ABSENT]
