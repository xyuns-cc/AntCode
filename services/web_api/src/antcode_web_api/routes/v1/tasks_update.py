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
    try:
        await ensure_worker_access(task_data, current_user)
        task = await scheduler_service.update_task(task_id, task_data, current_user.user_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        return success_response(create_task_response(task), message=Messages.UPDATED_SUCCESS)
    except HTTPException:
        raise
    except ValueError as exc:
        logger.warning(f"任务更新参数非法: {exc}")
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error(f"更新任务失败: {exc}")
        raise HTTPException(status_code=500, detail="更新任务失败") from exc


__all__ = ["update_task"]
