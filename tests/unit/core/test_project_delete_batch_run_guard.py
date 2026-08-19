"""项目删除的在途执行判定必须覆盖爬取批次 run。

批次 run 的 ``task_id`` 恒为 ``TASK_ID_ABSENT``(0)，``scheduled_tasks`` 里永远没有
对应行。只按 ``TaskRun.filter(task_id__in=<项目的 task_ids>)`` 查在途 run，会让
"批次正在爬"的项目通过无活跃执行检查被删掉：``CrawlBatch`` 行随项目一起没了，
在途 run 既查不到批次也查不到项目，变成永久孤儿。

这些用例**不**替换作用域解析或状态判定——那正是会坏的那一环，mock 掉等于把被测
逻辑挖走。全部真表真查（内存 sqlite + 真实模型 + 真事务）。
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from antcode_core.application.services.projects.project_cascade_delete import (
    delete_project_cascade,
)
from antcode_core.application.services.projects.project_delete_scope import (
    ACTIVE_RUN_REJECTION,
    lock_project_scope,
)
from antcode_core.domain.models import CrawlBatch, Project, Task, TaskRun
from antcode_core.domain.models.enums import (
    DispatchStatus,
    ProjectType,
    ScheduleType,
    TaskStatus,
    TaskType,
)
from antcode_core.domain.models.task_run import TASK_ID_ABSENT
from tortoise import Tortoise
from tortoise.transactions import in_transaction

OWNER_ID = 7
BATCH_PUBLIC_ID = "batch-under-test"
FOREIGN_BATCH_PUBLIC_ID = "batch-other-project"


@pytest_asyncio.fixture
async def cascade_tables():
    """真实建表：判定要跨 Project / Task / TaskRun / CrawlBatch 四张表。"""
    await Tortoise.init(
        db_url="sqlite://:memory:",
        modules={"models": ["antcode_core.domain.models"]},
    )
    await Tortoise.generate_schemas()
    try:
        yield
    finally:
        await Tortoise.close_connections()


async def _create_project(name: str) -> Project:
    return await Project.create(name=name, user_id=OWNER_ID, type=ProjectType.RULE)


async def _create_batch(project: Project, public_id: str) -> CrawlBatch:
    return await CrawlBatch.create(
        public_id=public_id,
        project_id=project.id,
        name=f"batch-of-{project.name}",
        seed_urls=["https://example.invalid/seed"],
        user_id=OWNER_ID,
    )


async def _create_batch_run(batch_public_id: str, *, run_id: str, status: TaskStatus) -> TaskRun:
    """批次 run：哨兵 task_id + result_data 里的 crawl_batch_id，与派发侧一致。"""
    return await TaskRun.create(
        run_id=run_id,
        task_id=TASK_ID_ABSENT,
        status=status,
        dispatch_status=DispatchStatus.DISPATCHED,
        result_data={"crawl_batch_id": batch_public_id, "seed_url": "https://example.invalid/seed"},
    )


async def _create_task_run(project: Project, *, status: TaskStatus) -> TaskRun:
    task = await Task.create(
        name=f"task-of-{project.name}",
        project_id=project.id,
        task_type=TaskType.RULE,
        schedule_type=ScheduleType.ONCE,
        user_id=OWNER_ID,
    )
    return await TaskRun.create(
        run_id=f"scheduled-run-{task.id}",
        task_id=task.id,
        status=status,
        dispatch_status=DispatchStatus.DISPATCHED,
    )


@pytest.mark.asyncio
async def test_in_flight_batch_run_blocks_project_delete(cascade_tables):
    """摘掉修复即变红：项目在爬时必须拒绝删除，而不是删完留孤儿 run。"""
    project = await _create_project("proj-with-live-batch")
    await _create_batch(project, BATCH_PUBLIC_ID)
    await _create_batch_run(BATCH_PUBLIC_ID, run_id="live-batch-run", status=TaskStatus.RUNNING)

    with pytest.raises(ValueError, match=ACTIVE_RUN_REJECTION):
        await delete_project_cascade(project.id)

    # 拒绝必须是真拒绝：项目/批次/run 三者一个都不能少
    assert await Project.filter(id=project.id).exists()
    assert await CrawlBatch.filter(public_id=BATCH_PUBLIC_ID).exists()
    assert await TaskRun.filter(run_id="live-batch-run").exists()


@pytest.mark.asyncio
async def test_dispatching_batch_run_blocks_project_delete(cascade_tables):
    """已派发未开跑同样算在途——DISPATCHING 不是终态。"""
    project = await _create_project("proj-with-dispatching-batch")
    await _create_batch(project, BATCH_PUBLIC_ID)
    await _create_batch_run(BATCH_PUBLIC_ID, run_id="dispatching-run", status=TaskStatus.DISPATCHING)

    with pytest.raises(ValueError, match=ACTIVE_RUN_REJECTION):
        async with in_transaction() as conn:
            await lock_project_scope(conn, project.id)


@pytest.mark.asyncio
async def test_terminal_batch_run_does_not_block_project_delete(cascade_tables):
    """终态批次 run 不该拦删除，否则跑过一次爬取的项目永远删不掉。"""
    project = await _create_project("proj-with-finished-batch")
    await _create_batch(project, BATCH_PUBLIC_ID)
    await _create_batch_run(BATCH_PUBLIC_ID, run_id="finished-batch-run", status=TaskStatus.SUCCESS)

    async with in_transaction() as conn:
        scope = await lock_project_scope(conn, project.id)

    assert scope.batch_ids == (BATCH_PUBLIC_ID,)
    assert scope.task_ids == ()


@pytest.mark.asyncio
async def test_other_projects_live_batch_run_does_not_block_delete(cascade_tables):
    """归属判定不能退化成"有任何在途批次 run 就全局拦"。

    别的项目正在爬，本项目（自己也有批次，只是都跑完了）必须还能删。
    """
    project = await _create_project("proj-idle")
    other = await _create_project("proj-busy")
    await _create_batch(project, BATCH_PUBLIC_ID)
    await _create_batch(other, FOREIGN_BATCH_PUBLIC_ID)
    await _create_batch_run(FOREIGN_BATCH_PUBLIC_ID, run_id="foreign-live-run", status=TaskStatus.RUNNING)

    async with in_transaction() as conn:
        scope = await lock_project_scope(conn, project.id)

    assert scope.batch_ids == (BATCH_PUBLIC_ID,)

    # 反向：正在爬的那个项目必须被拦住
    with pytest.raises(ValueError, match=ACTIVE_RUN_REJECTION):
        async with in_transaction() as conn:
            await lock_project_scope(conn, other.id)


@pytest.mark.asyncio
async def test_in_flight_scheduled_task_run_still_blocks(cascade_tables):
    """计划任务 run 这一族的原有判定不能被改回归掉。"""
    project = await _create_project("proj-with-live-task")
    await _create_task_run(project, status=TaskStatus.RUNNING)

    with pytest.raises(ValueError, match=ACTIVE_RUN_REJECTION):
        async with in_transaction() as conn:
            await lock_project_scope(conn, project.id)


@pytest.mark.asyncio
async def test_scope_reports_locked_tasks_and_batches(cascade_tables):
    """作用域必须同时带回两族载体，供后续删除与 Redis 清理使用。"""
    project = await _create_project("proj-mixed")
    await _create_batch(project, BATCH_PUBLIC_ID)
    run = await _create_task_run(project, status=TaskStatus.SUCCESS)

    async with in_transaction() as conn:
        scope = await lock_project_scope(conn, project.id)

    assert scope.project_id == project.id
    assert scope.project_public_id == project.public_id
    assert scope.task_ids == (run.task_id,)
    assert scope.batch_ids == (BATCH_PUBLIC_ID,)
