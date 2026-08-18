"""Single-task update handler."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from antcode_core.application.services.scheduler.scheduler_service import scheduler_service
from antcode_core.domain.schemas.task import TaskUpdateRequest
from fastapi import HTTPException
from loguru import logger

from antcode_web_api.response import Messages
from antcode_web_api.response import success as success_response


async def update_task(
    task_id: str,
    task_data: TaskUpdateRequest,
    current_user: Any,
    *,
    ensure_worker_access: Callable[[Any, Any], Awaitable[None]],
    create_task_response: Callable[[Any], Any],
):
    """更新任务。

    ``ValueError -> 400`` 只覆盖写入阶段：那里 ValueError 确实只由入参校验
    （触发器配置、Worker 不存在）抛出。响应组装阶段留在 try 之外——它抛
    ValueError 表示查询投影缺字段，是服务端缺陷，必须以 5xx 暴露而不是被
    归类成"参数非法"返回 400（那正是"写入已生效却报失败"的来源）。
    """
    task = await _write_task_update(task_id, task_data, current_user, ensure_worker_access=ensure_worker_access)
    return success_response(create_task_response(task), message=Messages.UPDATED_SUCCESS)


async def _write_task_update(
    task_id: str,
    task_data: TaskUpdateRequest,
    current_user: Any,
    *,
    ensure_worker_access: Callable[[Any, Any], Awaitable[None]],
):
    try:
        await ensure_worker_access(task_data, current_user)
        task = await scheduler_service.update_task(task_id, task_data, current_user.user_id)
    except HTTPException:
        raise
    except ValueError as exc:
        logger.warning(f"任务更新参数非法: {exc}")
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error(f"更新任务失败: {exc}")
        raise HTTPException(status_code=500, detail="更新任务失败") from exc
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


__all__ = ["update_task"]
