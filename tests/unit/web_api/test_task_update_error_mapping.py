from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.domain.schemas.task import TaskUpdateRequest
from antcode_web_api.routes.v1 import tasks
from fastapi import HTTPException

BAD_REQUEST = 400


@pytest.mark.asyncio
async def test_task_update_maps_schedule_validation_error_to_bad_request(monkeypatch, http_request) -> None:
    validation_error = ValueError("DATE 调度必须提供 scheduled_time")
    monkeypatch.setattr(tasks, "_ensure_specified_worker_access", AsyncMock())
    monkeypatch.setattr(
        tasks.scheduler_service,
        "update_task",
        AsyncMock(side_effect=validation_error),
    )

    with pytest.raises(HTTPException) as captured:
        await tasks.update_task(
            "task-public",
            TaskUpdateRequest(name="updated-task"),
            SimpleNamespace(user_id=7),
            http_request=http_request,
        )

    assert captured.value.status_code == BAD_REQUEST
    assert captured.value.detail == "DATE 调度必须提供 scheduled_time"


@pytest.mark.asyncio
async def test_response_projection_failure_is_not_reported_as_bad_request(monkeypatch, http_request) -> None:
    """写入已提交后响应组装失败，不能再被当成"参数非法"返回 400。

    这正是走查里"PUT 返回 400 但 DB 改动已生效"的误分类来源：
    ``_resolve_public_id`` 抛的是 ValueError，与入参校验错误撞在同一个 except 上。
    """
    bare_task = SimpleNamespace(public_id="task-public", name="updated-task")
    monkeypatch.setattr(tasks, "_ensure_specified_worker_access", AsyncMock())
    monkeypatch.setattr(tasks.scheduler_service, "update_task", AsyncMock(return_value=bare_task))

    with pytest.raises(ValueError, match="响应对象缺少 project_public_id"):
        await tasks.update_task(
            "task-public",
            TaskUpdateRequest(name="updated-task"),
            SimpleNamespace(user_id=7),
            http_request=http_request,
        )
