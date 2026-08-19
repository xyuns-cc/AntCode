"""爬取批次 run 的读取 / 取消 / 日志访问鉴权。

这些用例**不**替换 ``resolve_run_owner_id``：所有者解析恰恰是会坏的那一环，mock 掉它
等于把被测逻辑挖走。全部真表真查（TaskRun / Task / CrawlBatch / User 四张表）。

摘掉修复即变红：把任一入口改回"只按 ``Task.get_or_none(id=run.task_id)`` 判"，批次
用例立刻拿到 404 / None —— ``scheduled_tasks`` 里永远没有 id=``TASK_ID_ABSENT`` 的行。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import pytest_asyncio
from antcode_core.domain.models import CrawlBatch, Task, TaskRun, User
from antcode_core.domain.models.enums import DispatchStatus, ScheduleType, TaskStatus, TaskType
from antcode_core.domain.models.task_run import TASK_ID_ABSENT
from antcode_core.domain.models.user import UserRole
from fastapi import HTTPException, status
from tortoise import Tortoise

OWNER_ID = 7
STRANGER_ID = 8
ADMIN_ID = 1
PROJECT_ID = 11
SCHEDULED_TASK_ID = 21
ORPHAN_TASK_ID = 999
BATCH_PUBLIC_ID = "batch-1"
BATCH_RUN_ID = "crawl-batch-run"
TASK_RUN_ID = "scheduled-task-run"
ORPHAN_RUN_ID = "orphan-run"


@pytest_asyncio.fixture
async def access_tables():
    await Tortoise.init(
        db_url="sqlite://:memory:",
        modules={"models": ["antcode_core.domain.models"]},
    )
    await Tortoise.generate_schemas()
    try:
        yield
    finally:
        await Tortoise.close_connections()


async def _create_user(user_id: int, *, is_admin: bool = False) -> User:
    # is_admin 由 User.save 从 role 派生，直接传布尔会被覆盖回 False。
    return await User.create(
        id=user_id,
        username=f"user-{user_id}",
        password_hash="x",
        role=UserRole.ADMIN if is_admin else UserRole.USER,
    )


async def _create_batch_run(*, run_status: TaskStatus = TaskStatus.RUNNING) -> TaskRun:
    await CrawlBatch.create(
        public_id=BATCH_PUBLIC_ID,
        project_id=PROJECT_ID,
        name="批次",
        user_id=OWNER_ID,
    )
    return await TaskRun.create(
        run_id=BATCH_RUN_ID,
        task_id=TASK_ID_ABSENT,
        status=run_status,
        dispatch_status=DispatchStatus.PENDING,
        result_data={"crawl_batch_id": BATCH_PUBLIC_ID},
    )


async def _create_scheduled_task_run(*, run_status: TaskStatus = TaskStatus.RUNNING) -> TaskRun:
    await Task.create(
        id=SCHEDULED_TASK_ID,
        name="计划任务",
        project_id=PROJECT_ID,
        task_type=TaskType.CODE,
        schedule_type=ScheduleType.ONCE,
        user_id=OWNER_ID,
    )
    return await TaskRun.create(
        run_id=TASK_RUN_ID,
        task_id=SCHEDULED_TASK_ID,
        status=run_status,
        dispatch_status=DispatchStatus.PENDING,
    )


async def _create_orphan_task_run(*, run_status: TaskStatus = TaskStatus.RUNNING) -> TaskRun:
    """真丢 Task 行的孤儿 run：task_id 非哨兵但 scheduled_tasks 里查不到。"""
    return await TaskRun.create(
        run_id=ORPHAN_RUN_ID,
        task_id=ORPHAN_TASK_ID,
        status=run_status,
        dispatch_status=DispatchStatus.PENDING,
    )


# --------------------------------------------------------------------------
# resolve_run_owner_id：哨兵豁免不得退化成"任务不存在就当没有"
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_run_owner_resolves_from_crawl_batch(access_tables) -> None:
    from antcode_core.application.services.run_ownership import resolve_run_owner_id

    run = await _create_batch_run()

    assert await resolve_run_owner_id(run) == OWNER_ID


@pytest.mark.asyncio
async def test_orphan_task_run_still_resolves_to_no_owner(access_tables) -> None:
    """哨兵豁免只免批次 run；真丢 Task 行的孤儿 run 照旧无主。"""
    from antcode_core.application.services.run_ownership import resolve_run_owner_id

    run = await _create_orphan_task_run()

    assert await resolve_run_owner_id(run) is None


@pytest.mark.asyncio
async def test_batch_run_with_deleted_crawl_batch_has_no_owner(access_tables) -> None:
    run = await _create_batch_run()
    await CrawlBatch.filter(public_id=BATCH_PUBLIC_ID).delete()

    from antcode_core.application.services.run_ownership import resolve_run_owner_id

    assert await resolve_run_owner_id(run) is None


# --------------------------------------------------------------------------
# get_execution_with_permission：8 个 run 级端点共用的鉴权闸
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_owner_can_read_own_batch_run(access_tables) -> None:
    from antcode_core.application.services.scheduler.scheduler_service import scheduler_service

    await _create_user(OWNER_ID)
    await _create_batch_run()

    execution = await scheduler_service.get_execution_with_permission(BATCH_RUN_ID, OWNER_ID)

    assert execution is not None
    assert execution.run_id == BATCH_RUN_ID


@pytest.mark.asyncio
async def test_stranger_cannot_read_foreign_batch_run(access_tables) -> None:
    """放行批次 run 不等于放弃鉴权：非所有者仍然看不到。"""
    from antcode_core.application.services.scheduler.scheduler_service import scheduler_service

    await _create_user(OWNER_ID)
    await _create_user(STRANGER_ID)
    await _create_batch_run()

    assert await scheduler_service.get_execution_with_permission(BATCH_RUN_ID, STRANGER_ID) is None


@pytest.mark.asyncio
async def test_stranger_cannot_read_orphan_run(access_tables) -> None:
    from antcode_core.application.services.scheduler.scheduler_service import scheduler_service

    await _create_user(STRANGER_ID)
    await _create_orphan_task_run()

    assert await scheduler_service.get_execution_with_permission(ORPHAN_RUN_ID, STRANGER_ID) is None


@pytest.mark.asyncio
async def test_task_run_owner_still_reads_own_run(access_tables) -> None:
    from antcode_core.application.services.scheduler.scheduler_service import scheduler_service

    await _create_user(OWNER_ID)
    await _create_scheduled_task_run()

    execution = await scheduler_service.get_execution_with_permission(TASK_RUN_ID, OWNER_ID)

    assert execution is not None
    assert execution.task_public_id is not None


# --------------------------------------------------------------------------
# POST /runs/{run_id}/cancel 的可取消性闸门（本轮报告的缺陷）
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_run_passes_cancellable_gate(access_tables) -> None:
    """回归: 批次 run 此前必然 404 '关联任务不存在'，无法单条取消。"""
    from antcode_web_api.routes.v1 import runs

    await _create_user(OWNER_ID)
    await _create_batch_run()

    execution = await runs._get_cancellable_execution(BATCH_RUN_ID, OWNER_ID)

    assert execution.run_id == BATCH_RUN_ID


@pytest.mark.asyncio
async def test_orphan_run_is_still_refused_by_cancellable_gate(access_tables) -> None:
    """管理员绕过所有权闸，但无主 run 仍不可取消 —— 豁免没有变成放行一切。"""
    from antcode_web_api.routes.v1 import runs

    await _create_user(ADMIN_ID, is_admin=True)
    await _create_orphan_task_run()

    with pytest.raises(HTTPException) as exc_info:
        await runs._get_cancellable_execution(ORPHAN_RUN_ID, ADMIN_ID)

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_terminal_batch_run_is_refused_before_owner_lookup(access_tables) -> None:
    from antcode_web_api.routes.v1 import runs

    await _create_user(OWNER_ID)
    await _create_batch_run(run_status=TaskStatus.SUCCESS)

    with pytest.raises(HTTPException) as exc_info:
        await runs._get_cancellable_execution(BATCH_RUN_ID, OWNER_ID)

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST


# --------------------------------------------------------------------------
# 日志流 / Worker 日志的 run 级鉴权闸
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_owner_passes_log_stream_gate(access_tables) -> None:
    from antcode_web_api.streams import log_stream_access

    user = await _create_user(OWNER_ID)
    await _create_batch_run()

    execution = await log_stream_access.verify_execution_access(BATCH_RUN_ID, user)

    assert execution.run_id == BATCH_RUN_ID
    assert await log_stream_access.execution_access_still_valid(BATCH_RUN_ID, OWNER_ID) is True


@pytest.mark.asyncio
async def test_stranger_is_refused_by_log_stream_gate(access_tables) -> None:
    from antcode_web_api.streams import log_stream_access

    await _create_user(OWNER_ID)
    stranger = await _create_user(STRANGER_ID)
    await _create_batch_run()

    with pytest.raises(HTTPException) as exc_info:
        await log_stream_access.verify_execution_access(BATCH_RUN_ID, stranger)

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
    assert await log_stream_access.execution_access_still_valid(BATCH_RUN_ID, STRANGER_ID) is False


@pytest.mark.asyncio
async def test_batch_owner_passes_worker_run_access_gate(access_tables) -> None:
    from antcode_web_api.routes.v1 import workers

    await _create_user(OWNER_ID)
    await _create_batch_run()

    await workers._require_run_access(BATCH_RUN_ID, SimpleNamespace(user_id=OWNER_ID))


@pytest.mark.asyncio
async def test_stranger_is_refused_by_worker_run_access_gate(access_tables) -> None:
    from antcode_web_api.routes.v1 import workers

    await _create_user(OWNER_ID)
    await _create_user(STRANGER_ID)
    await _create_batch_run()

    with pytest.raises(HTTPException) as exc_info:
        await workers._require_run_access(BATCH_RUN_ID, SimpleNamespace(user_id=STRANGER_ID))

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
