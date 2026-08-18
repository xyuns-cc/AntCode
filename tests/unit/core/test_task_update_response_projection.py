"""``update_task`` 必须返回带完整投影的任务对象。

线上表现：``PUT /api/v1/tasks/{id}`` 返回 400「响应对象缺少 project_public_id」，
但 DB 改动已经生效——因为事务里锁到的是裸 ORM 行，缺投影字段，
``TaskResponseBuilder._resolve_public_id`` 抛 ValueError 又被路由归类成"参数非法"。
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from antcode_core.application.services.scheduler.scheduler_service import scheduler_service
from antcode_core.domain.models import Project, Task, User
from antcode_core.domain.models.enums import ProjectType, ScheduleType, TaskType
from antcode_core.domain.schemas.task import TaskUpdateRequest
from tortoise import Tortoise

OWNER_ID = 7
ORIGINAL_NAME = "任务-改前"
UPDATED_NAME = "任务-改后"


@pytest_asyncio.fixture
async def task_row():
    await Tortoise.init(
        db_url="sqlite://:memory:",
        modules={"models": ["antcode_core.domain.models"]},
    )
    await Tortoise.generate_schemas()
    try:
        await User.create(id=OWNER_ID, username="owner", password_hash="x")
        project = await Project.create(name="项目", type=ProjectType.CODE, user_id=OWNER_ID)
        task = await Task.create(
            name=ORIGINAL_NAME,
            project_id=project.id,
            task_type=TaskType.CODE,
            schedule_type=ScheduleType.ONCE,
            user_id=OWNER_ID,
        )
        yield task, project
    finally:
        await Tortoise.close_connections()


@pytest.mark.asyncio
async def test_update_returns_task_carrying_response_projection(task_row) -> None:
    task, project = task_row

    updated = await scheduler_service.update_task(task.public_id, TaskUpdateRequest(name=UPDATED_NAME), OWNER_ID)

    assert updated is not None
    # 响应构造依赖的两个投影字段必须齐备，否则路由端就是那条 400。
    assert updated.project_public_id == project.public_id
    assert updated.created_by_public_id is not None
    assert updated.name == UPDATED_NAME


@pytest.mark.asyncio
async def test_update_actually_persists_before_projection(task_row) -> None:
    task, _ = task_row

    await scheduler_service.update_task(task.public_id, TaskUpdateRequest(name=UPDATED_NAME), OWNER_ID)

    assert (await Task.get(id=task.id)).name == UPDATED_NAME
