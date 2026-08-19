"""任务批量操作接口 (batch-delete / batch)。

P2 拆分自 tasks.py: 2 个 handler + 1 helper + 1 schema:
- POST /tasks/batch-delete (batch_delete_tasks)
- POST /tasks/batch (batch_operate_tasks)

URL / DI 与旧实现一致；返回体在原有字段之上**增量**加了 failures（逐项失败
原因，见 settlement_http 与 contracts/http/batch_delete_failures.json），旧的
success_count / failed_count / failed_ids / success_ids 一个都没动。
TaskBatchRequest 顶层在 tasks.py re-export, 保证 tests import
tasks.TaskBatchRequest 继续可命中。
"""

from __future__ import annotations

from typing import Any

from antcode_core.application.services.scheduler.scheduler_service import scheduler_service
from antcode_core.common.security.auth import get_current_user
from antcode_core.domain.schemas.common import BaseResponse
from antcode_core.domain.schemas.task import (
    TaskUpdateRequest as TaskUpdate,
)
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from antcode_web_api.response import Messages
from antcode_web_api.response import (
    success as success_response,
)
from antcode_web_api.routes.v1.settlement_http import BatchReasons, collect_batch_outcome
from antcode_web_api.routes.v1.task_cancel import cancel_latest_task_run
from antcode_web_api.utils.batch_inputs import bounded_distinct_ids

# 逐项失败原因。批量删除最常见的拒绝是"该任务还有未终态执行"，
# 单条删除会在 409 里点名在线 Worker，批量入口必须给出同样的线索。
DELETE_REASONS = BatchReasons(
    action="删除任务",
    missing="任务不存在或无权删除",
    unexpected="删除任务失败",
)
OPERATE_REASONS = BatchReasons(
    action="批量操作任务",
    missing="操作未生效：任务不存在、无权访问，或当前状态不允许该操作",
    unexpected="操作任务失败",
)


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
    outcome = await collect_batch_outcome(
        task_ids,
        lambda task_id: scheduler_service.delete_task(task_id, current_user.user_id),
        DELETE_REASONS,
    )
    success_count = len(outcome.success_ids)
    failed_count = len(outcome.failures)
    return success_response(
        outcome.fields("failed_ids"),
        message=f"成功删除 {success_count} 个任务" + (f"，{failed_count} 个失败" if failed_count > 0 else ""),
    )


async def batch_operate_tasks(request: TaskBatchRequest, current_user):
    """批量操作任务"""
    if not request.task_ids:
        raise HTTPException(status_code=400, detail="task_ids不能为空")

    outcome = await collect_batch_outcome(
        request.task_ids,
        lambda task_id: _operate_task(task_id, request.action, current_user.user_id),
        OPERATE_REASONS,
    )
    return success_response(
        {**outcome.fields("failed_ids"), "success_ids": list(outcome.success_ids)},
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
