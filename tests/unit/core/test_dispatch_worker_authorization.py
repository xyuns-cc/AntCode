"""派发前 Worker use 权限复验。

这些用例**不**替换 ``_load_task_run_owners``：所有者解析恰恰是会坏的那一环，
mock 掉它等于把被测逻辑挖走（旧版两条用例就是这样假绿的——批次派发线上
100% 报"派发任务缺少所有者: task_ids=[0]"，它们仍然全绿）。这里用真表真查。
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from antcode_core.application.services.workers import dispatch_authorization
from antcode_core.domain.models import CrawlBatch, Task, TaskRun, User, UserWorkerPermission
from antcode_core.domain.models.enums import (
    DispatchStatus,
    ScheduleType,
    TaskStatus,
    TaskType,
    WorkerPermission,
)
from antcode_core.domain.models.task_run import TASK_ID_ABSENT
from antcode_core.domain.models.user import UserRole
from tortoise import Tortoise

WORKER_ID = 3
OWNER_ID = 7
ADMIN_ID = 1
PROJECT_ID = 11
SCHEDULED_TASK_ID = 21
BATCH_PUBLIC_ID = "batch-1"
BATCH_RUN_ID = "crawl-batch-run"
TASK_RUN_ID = "scheduled-task-run"


@pytest_asyncio.fixture
async def owner_tables():
    """真实建表：所有者解析要跨 TaskRun / Task / CrawlBatch / 权限四张表。"""
    await Tortoise.init(
        db_url="sqlite://:memory:",
        modules={"models": ["antcode_core.domain.models"]},
    )
    await Tortoise.generate_schemas()
    try:
        yield
    finally:
        await Tortoise.close_connections()


async def _create_user(user_id: int, *, is_admin: bool) -> None:
    # is_admin 由 User.save 从 role 派生，直接传布尔会被覆盖回 False。
    await User.create(
        id=user_id,
        username=f"user-{user_id}",
        password_hash="x",
        role=UserRole.ADMIN if is_admin else UserRole.USER,
    )


async def _grant_worker_use(user_id: int) -> None:
    await UserWorkerPermission.create(
        user_id=user_id,
        worker_id=WORKER_ID,
        permission=WorkerPermission.USE.value,
    )


async def _create_batch_run(*, result_data: dict | None) -> None:
    await CrawlBatch.create(
        public_id=BATCH_PUBLIC_ID,
        project_id=PROJECT_ID,
        name="批次",
        user_id=OWNER_ID,
    )
    await TaskRun.create(
        run_id=BATCH_RUN_ID,
        task_id=TASK_ID_ABSENT,
        status=TaskStatus.PENDING,
        dispatch_status=DispatchStatus.PENDING,
        result_data=result_data,
    )


async def _create_scheduled_task_run(owner_id: int) -> None:
    await Task.create(
        id=SCHEDULED_TASK_ID,
        name="计划任务",
        project_id=PROJECT_ID,
        task_type=TaskType.CODE,
        schedule_type=ScheduleType.ONCE,
        user_id=owner_id,
    )
    await TaskRun.create(
        run_id=TASK_RUN_ID,
        task_id=SCHEDULED_TASK_ID,
        status=TaskStatus.PENDING,
        dispatch_status=DispatchStatus.PENDING,
    )


@pytest.mark.asyncio
async def test_batch_run_owner_resolves_from_crawl_batch(owner_tables) -> None:
    """回归: 批次 run 没有 Task 行，所有者必须由 CrawlBatch 解析出来。"""
    await _create_user(OWNER_ID, is_admin=False)
    await _grant_worker_use(OWNER_ID)
    await _create_batch_run(result_data={"crawl_batch_id": BATCH_PUBLIC_ID})

    await dispatch_authorization.require_task_run_worker_use_access([{"run_id": BATCH_RUN_ID}], WORKER_ID)


@pytest.mark.asyncio
async def test_batch_run_owner_without_worker_use_permission_is_denied(owner_tables) -> None:
    """解析出所有者不等于放行：批次所有者没有 use 权限照样拒。"""
    await _create_user(OWNER_ID, is_admin=False)
    await _create_batch_run(result_data={"crawl_batch_id": BATCH_PUBLIC_ID})

    with pytest.raises(dispatch_authorization.DispatchWorkerAccessDenied, match="use 权限"):
        await dispatch_authorization.require_task_run_worker_use_access([{"run_id": BATCH_RUN_ID}], WORKER_ID)


@pytest.mark.asyncio
async def test_batch_run_without_crawl_batch_id_is_denied(owner_tables) -> None:
    await _create_user(OWNER_ID, is_admin=False)
    await _grant_worker_use(OWNER_ID)
    await _create_batch_run(result_data={"seed_url": "https://a.test"})

    with pytest.raises(dispatch_authorization.DispatchWorkerAccessDenied, match="缺少 crawl_batch_id"):
        await dispatch_authorization.require_task_run_worker_use_access([{"run_id": BATCH_RUN_ID}], WORKER_ID)


@pytest.mark.asyncio
async def test_scheduled_task_run_owner_still_resolves_from_task(owner_tables) -> None:
    await _create_user(OWNER_ID, is_admin=False)
    await _grant_worker_use(OWNER_ID)
    await _create_scheduled_task_run(OWNER_ID)

    await dispatch_authorization.require_task_run_worker_use_access([{"run_id": TASK_RUN_ID}], WORKER_ID)


@pytest.mark.asyncio
async def test_admin_owner_needs_no_explicit_permission(owner_tables) -> None:
    await _create_user(ADMIN_ID, is_admin=True)
    await _create_scheduled_task_run(ADMIN_ID)

    await dispatch_authorization.require_task_run_worker_use_access([{"run_id": TASK_RUN_ID}], WORKER_ID)


@pytest.mark.asyncio
async def test_mixed_batch_and_task_runs_resolve_in_one_pass(owner_tables) -> None:
    await _create_user(OWNER_ID, is_admin=False)
    await _grant_worker_use(OWNER_ID)
    await _create_batch_run(result_data={"crawl_batch_id": BATCH_PUBLIC_ID})
    await _create_scheduled_task_run(OWNER_ID)

    await dispatch_authorization.require_task_run_worker_use_access(
        [{"run_id": BATCH_RUN_ID}, {"run_id": TASK_RUN_ID}],
        WORKER_ID,
    )


@pytest.mark.asyncio
async def test_missing_task_run_row_is_denied(owner_tables) -> None:
    with pytest.raises(dispatch_authorization.DispatchWorkerAccessDenied, match="缺少 TaskRun 归属记录"):
        await dispatch_authorization.require_task_run_worker_use_access([{"run_id": "unknown"}], WORKER_ID)
