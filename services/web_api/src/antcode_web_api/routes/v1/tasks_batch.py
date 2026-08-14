"""任务批量操作接口 (batch-delete / batch)。

P2 拆分自 tasks.py: 2 个 handler + 1 helper + 1 schema:
- POST /tasks/batch-delete (batch_delete_tasks)
- POST /tasks/batch (batch_operate_tasks)

契约 (URL / DI / 返回) 与旧实现一致。TaskBatchRequest 顶层在 tasks.py
re-export, 保证 tests import tasks.TaskBatchRequest 继续可命中。
"""

from __future__ import annotations

from typing import Any

from antcode_core.application.services.scheduler.scheduler_service import scheduler_service
from antcode_core.application.services.workers.run_settlement_guard import RunSettlementGuardUnavailable
from antcode_core.common.security.auth import get_current_user
from antcode_core.domain.schemas.common import BaseResponse
from antcode_core.domain.schemas.task import (
    TaskUpdateRequest as TaskUpdate,
)
from fastapi import Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field, field_validator

from antcode_web_api.response import Messages
from antcode_web_api.response import (
    success as success_response,
)
from antcode_web_api.routes.v1.task_cancel import cancel_latest_task_run
from antcode_web_api.utils.batch_inputs import bounded_distinct_ids


class TaskBatchRequest(BaseModel):
    task_ids: list[str] = Field(default_factory=list, max_length=100)
    action: str = Field(..., description="start/stop/cancel/delete/enable/disable")
    execution_config: dict[str, Any] | None = None

    @field_validator("task_ids")
    @classmethod
    def reject_duplicate_task_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("task_ids 不允许重复")
        return value


async def _operate_task(task_id: str, action: str, user_id: int) -> bool:
    if action == "delete":
        return bool(await scheduler_service.delete_task(task_id, user_id))
    if action == "enable":
        return bool(await scheduler_service.update_task(task_id, TaskUpdate(is_active=True), user_id))
    if action == "disable":
        return bool(await scheduler_service.update_task(task_id, TaskUpdate(is_active=False), user_id))
    if action == "start":
        return bool(await scheduler_service.trigger_task_by_user(task_id, user_id))
    if action == "stop":
        return bool(await scheduler_service.pause_task_by_user(task_id, user_id))
    if action == "cancel":
        return await cancel_latest_task_run(task_id, user_id)
    raise HTTPException(status_code=400, detail="不支持的操作类型")


async def batch_delete_tasks(request: dict, current_user):
    """批量删除任务"""
    task_ids = bounded_distinct_ids(request.get("task_ids"), "task_ids")

    success_count = 0
    failed_count = 0
    failed_ids = []

    for task_id in task_ids:
        try:
            deleted = await scheduler_service.delete_task(task_id, current_user.user_id)
            if deleted:
                success_count += 1
            else:
                failed_count += 1
                failed_ids.append(task_id)
        except RunSettlementGuardUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as e:
            logger.warning(f"删除任务 {task_id} 失败: {e}")
            failed_count += 1
            failed_ids.append(task_id)

    return success_response(
        {
            "success_count": success_count,
            "failed_count": failed_count,
            "failed_ids": failed_ids,
        },
        message=f"成功删除 {success_count} 个任务" + (f"，{failed_count} 个失败" if failed_count > 0 else ""),
    )


async def batch_operate_tasks(request: TaskBatchRequest, current_user):
    """批量操作任务"""
    if not request.task_ids:
        raise HTTPException(status_code=400, detail="task_ids不能为空")

    success_ids: list[str] = []
    failed_ids: list[str] = []

    for task_id in request.task_ids:
        try:
            succeeded = await _operate_task(task_id, request.action, current_user.user_id)
            if succeeded:
                success_ids.append(task_id)
            else:
                failed_ids.append(task_id)
        except RunSettlementGuardUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception:
            failed_ids.append(task_id)

    return success_response(
        {
            "success_count": len(success_ids),
            "failed_count": len(failed_ids),
            "success_ids": success_ids,
            "failed_ids": failed_ids,
        },
        message=Messages.OPERATION_SUCCESS,
    )


def register_batch_routes(router) -> None:
    @router.post("/batch-delete", response_model=BaseResponse)
    async def _batch_delete_tasks(request: dict, current_user=Depends(get_current_user)):
        """批量删除任务"""
        return await batch_delete_tasks(request, current_user)

    @router.post("/batch", response_model=BaseResponse[dict])
    async def _batch_operate_tasks(request: TaskBatchRequest, current_user=Depends(get_current_user)):
        """批量操作任务"""
        return await batch_operate_tasks(request, current_user)


__all__ = [
    "TaskBatchRequest",
    "batch_delete_tasks",
    "batch_operate_tasks",
    "register_batch_routes",
]
