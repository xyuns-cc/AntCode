"""任务执行控制接口 (pause / resume / trigger / execute / toggle)。

P2 拆分自 tasks.py: 5 个 handler + 3 helper + 2 schema:
- POST /tasks/{task_id}/pause (pause_task)
- POST /tasks/{task_id}/resume (resume_task)
- POST /tasks/{task_id}/trigger (trigger_task)
- POST /tasks/{task_id}/execute (execute_task)
- PATCH /tasks/{task_id}/toggle (toggle_task)

契约 (URL / DI / 返回) 与旧实现一致。trigger/execute 通过共享去重锁触发，
并直接返回 Outbox 推导出的确定性 run_id。create_task_response 由
register_execute_routes 注入避免循环 import。
"""

from __future__ import annotations

from typing import Any

from antcode_core.application.services.scheduler.scheduler_service import scheduler_service
from antcode_core.common.security.auth import get_current_user
from antcode_core.domain.schemas.common import BaseResponse
from antcode_core.domain.schemas.task import TaskResponse
from antcode_core.domain.schemas.task import TaskUpdateRequest as TaskUpdate
from fastapi import Depends, HTTPException, Request
from loguru import logger
from pydantic import BaseModel

from antcode_web_api.response import Messages
from antcode_web_api.response import (
    success as success_response,
)
from antcode_web_api.routes.v1.mutation_audit import audit_task_executed, audit_task_updated


class TaskExecuteRequest(BaseModel):
    execution_config: dict[str, Any] | None = None
    environment_variables: dict[str, str] | None = None


class TaskToggleRequest(BaseModel):
    enabled: bool


async def _acquire_trigger_dedup_lock(task_id: str, user_id: int) -> bool:
    """5s 内禁止同一用户重复触发同一任务

    返回 True 表示获得锁；False 表示 5 秒内已经触发过。
    Redis 不可用时显式失败，避免重复触发绕过去重约束。
    """
    try:
        from antcode_core.infrastructure.redis import get_redis_client

        redis = await get_redis_client()
        lock_key = f"trigger_dedup:{task_id}:{user_id}"
        acquired = await redis.set(lock_key, "1", nx=True, ex=5)
        return bool(acquired)
    except Exception as exc:
        logger.exception("触发去重锁获取失败")
        raise HTTPException(status_code=503, detail="任务触发去重服务不可用") from exc


async def pause_task(task_id, current_user):
    """暂停任务"""
    try:
        paused = await scheduler_service.pause_task_by_user(task_id, current_user.user_id)
        if not paused:
            raise HTTPException(status_code=404, detail="Task not found")

        return success_response(None, message="任务已暂停")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"暂停任务失败: {e}")
        raise HTTPException(status_code=500, detail="暂停任务失败")


async def resume_task(task_id, current_user):
    """恢复任务"""
    try:
        resumed = await scheduler_service.resume_task_by_user(task_id, current_user.user_id)
        if not resumed:
            raise HTTPException(status_code=404, detail="Task not found")

        return success_response(None, message="任务已恢复")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"恢复任务失败: {e}")
        raise HTTPException(status_code=500, detail="恢复任务失败")


async def trigger_task(task_id, current_user, *, http_request):
    """立即触发任务

    - T15: Redis 锁去重，5s 内同 task_id+user 只允许触发一次
    - S6: 返回最新一次执行的 run_id，便于前端立即订阅日志
    """
    try:
        if not await _acquire_trigger_dedup_lock(str(task_id), current_user.user_id):
            raise HTTPException(status_code=409, detail="请勿连续触发同一任务")

        trigger_result = await scheduler_service.trigger_task_by_user(task_id, current_user.user_id)
        if not trigger_result:
            raise HTTPException(status_code=404, detail="Task not found")
        run_id = trigger_result if isinstance(trigger_result, str) else None
        await audit_task_executed(http_request, current_user, task_id=str(task_id), run_id=run_id)
        return success_response(
            {"task_id": str(task_id), "run_id": run_id, "triggered": True},
            message="任务已触发",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"触发任务失败: {e}")
        raise HTTPException(status_code=500, detail="触发任务失败")


async def execute_task(task_id: str, request: TaskExecuteRequest, current_user, *, http_request):
    """执行任务（触发立即执行）"""
    try:
        # P2 §4.4: execute 覆盖参数此前被静默忽略 —— 用户以为带覆盖执行，
        # 实际按任务原配置跑。单次覆盖执行的全链路（web_api → 事件 →
        # Master 调度 → 派发）尚未支持，显式拒绝而非静默丢弃。
        if request.execution_config or request.environment_variables:
            raise HTTPException(
                status_code=400,
                detail="execute 暂不支持单次覆盖 execution_config/environment_variables；请先更新任务配置后触发",
            )
        if not await _acquire_trigger_dedup_lock(str(task_id), current_user.user_id):
            raise HTTPException(status_code=409, detail="请勿连续触发同一任务")

        trigger_result = await scheduler_service.trigger_task_by_user(task_id, current_user.user_id)
        if not trigger_result:
            raise HTTPException(status_code=404, detail="Task not found")
        run_id = trigger_result if isinstance(trigger_result, str) else None
        await audit_task_executed(http_request, current_user, task_id=task_id, run_id=run_id)
        return success_response(
            {"task_id": task_id, "run_id": run_id, "triggered": True},
            message="任务已触发",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"执行任务失败: {e}")
        raise HTTPException(status_code=500, detail="执行任务失败")


async def toggle_task(task_id: str, request: TaskToggleRequest, current_user, *, http_request, create_task_response):
    """启用/禁用任务。它写的是 is_active，属于任务更新，同样要留痕。"""
    try:
        task = await scheduler_service.update_task(task_id, TaskUpdate(is_active=request.enabled), current_user.user_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        await audit_task_updated(http_request, current_user, task, changed_fields=["is_active"])
        return success_response(create_task_response(task), message=Messages.UPDATED_SUCCESS)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"切换任务状态失败: {e}")
        raise HTTPException(status_code=500, detail="切换任务状态失败")


def register_execute_routes(router, *, create_task_response) -> None:
    """挂载 5 个执行控制 handler。``create_task_response`` 由主 tasks 传入以避免循环 import。"""

    @router.post("/{task_id}/pause", response_model=BaseResponse)
    async def _pause_task(task_id, current_user=Depends(get_current_user)):
        """暂停任务"""
        return await pause_task(task_id, current_user)

    @router.post("/{task_id}/resume", response_model=BaseResponse)
    async def _resume_task(task_id, current_user=Depends(get_current_user)):
        """恢复任务"""
        return await resume_task(task_id, current_user)

    @router.post("/{task_id}/trigger", response_model=BaseResponse[dict])
    async def _trigger_task(task_id, current_user=Depends(get_current_user), *, http_request: Request):
        """立即触发任务

        - T15: Redis 锁去重，5s 内同 task_id+user 只允许触发一次
        - S6: 返回最新一次执行的 run_id，便于前端立即订阅日志
        """
        return await trigger_task(task_id, current_user, http_request=http_request)

    @router.post("/{task_id}/execute", response_model=BaseResponse[dict])
    async def _execute_task(
        task_id: str,
        request: TaskExecuteRequest,
        current_user=Depends(get_current_user),
        *,
        http_request: Request,
    ):
        """执行任务（触发立即执行）"""
        return await execute_task(task_id, request, current_user, http_request=http_request)

    @router.patch("/{task_id}/toggle", response_model=BaseResponse[TaskResponse])
    async def _toggle_task(
        task_id: str,
        request: TaskToggleRequest,
        current_user=Depends(get_current_user),
        *,
        http_request: Request,
    ):
        """启用/禁用任务"""
        return await toggle_task(
            task_id,
            request,
            current_user,
            http_request=http_request,
            create_task_response=create_task_response,
        )


__all__ = [
    "TaskExecuteRequest",
    "TaskToggleRequest",
    "execute_task",
    "pause_task",
    "register_execute_routes",
    "resume_task",
    "toggle_task",
    "trigger_task",
]
