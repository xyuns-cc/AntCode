"""任务运行接口"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from loguru import logger
from tortoise.exceptions import DoesNotExist

from antcode_web_api.response import Messages
from antcode_web_api.response import success as success_response
from antcode_core.common.security.auth import get_current_user
from antcode_core.domain.schemas.common import BaseResponse
from antcode_core.domain.schemas.logs import LogFileResponse
from antcode_core.domain.schemas.task import TaskRunResponse
from antcode_core.application.services.logs.task_log_service import task_log_service
from antcode_core.application.services.scheduler.scheduler_service import scheduler_service

runs_router = APIRouter()


@runs_router.get("/{run_id}", response_model=BaseResponse[TaskRunResponse])
async def get_run(run_id, current_user=Depends(get_current_user)):
    """获取执行详情"""
    try:
        execution = await scheduler_service.get_execution_with_permission(
            run_id, current_user.user_id
        )
        if not execution:
            raise HTTPException(status_code=404, detail="执行记录不存在或无权访问")

        return success_response(
            TaskRunResponse.from_orm(execution), message=Messages.QUERY_SUCCESS
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取执行详情失败: {e}")
        raise HTTPException(status_code=500, detail="获取执行详情失败")


@runs_router.post("/{run_id}/cancel", response_model=BaseResponse[dict])
async def cancel_run(run_id: str, current_user=Depends(get_current_user)):
    """
    取消正在执行的任务

    - 如果任务在 Worker 上运行，会发送取消指令到 Worker
    - 如果任务在队列中等待，会直接取消
    """
    from antcode_core.domain.models.enums import TaskStatus
    from antcode_core.domain.models.task import Task

    # 获取执行记录
    execution = await scheduler_service.get_execution_with_permission(
        run_id, current_user.user_id
    )
    if not execution:
        raise HTTPException(status_code=404, detail="执行记录不存在或无权访问")

    # 检查状态
    if execution.status not in (
        TaskStatus.PENDING,
        TaskStatus.DISPATCHING,
        TaskStatus.QUEUED,
        TaskStatus.RUNNING,
    ):
        raise HTTPException(
            status_code=400, detail=f"任务状态为 {execution.status.value}，无法取消"
        )

    # 获取任务信息
    task = await Task.get_or_none(id=execution.task_id)
    if not task:
        raise HTTPException(status_code=404, detail="关联任务不存在")

    cancelled = False

    # 如果任务正在 Worker 上运行，发送取消指令
    if execution.worker_id:
        try:
            from antcode_core.application.services.workers.worker_service import (
                worker_service,
            )
            from antcode_core.infrastructure.redis import get_redis_client

            worker = await worker_service.get_worker_by_id(execution.worker_id)
            if worker:
                redis = await get_redis_client()
                payload = {
                    "control_type": "cancel",
                    "task_id": execution.execution_id,
                    "run_id": execution.execution_id,
                    "reason": f"user_cancel:{current_user.user_id}",
                }
                await redis.xadd(f"antcode:control:{worker.public_id}", payload)
                cancelled = True
                if cancelled:
                    logger.info(f"已发送取消指令到 Worker: {worker.name}")
        except Exception as e:
            logger.warning(f"发送取消指令失败: {e}")

    # 更新数据库状态
    from antcode_core.application.services.scheduler.execution_status_service import (
        execution_status_service,
    )

    await execution_status_service.update_runtime_status(
        execution_id=execution.execution_id,
        status="cancelled",
        status_at=datetime.now(UTC),
        error_message=f"用户取消 (user_id={current_user.user_id})",
    )

    logger.info(f"执行已取消: {run_id}, 远程取消={cancelled}")

    return success_response(
        {
            "execution_id": run_id,
            "status": "cancelled",
            "remote_cancelled": cancelled,
        },
        message="任务已取消",
    )


@runs_router.get("/{run_id}/logs/file", response_model=BaseResponse[LogFileResponse])
async def get_run_log_file(
    run_id: str,  # 支持 public_id
    log_type: str = Query("output", pattern="^(output|error)$"),
    lines: int = Query(None, ge=1, le=10000),
    current_user=Depends(get_current_user),
):
    """获取执行日志文件内容"""
    try:
        execution = await scheduler_service.get_execution_with_permission(
            run_id, current_user.user_id
        )
        if not execution:
            raise HTTPException(status_code=404, detail="执行记录不存在或无权访问")

        # 确定日志文件路径
        log_file_path = execution.log_file_path if log_type == "output" else execution.error_log_path

        if not log_file_path:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="日志文件路径不存在")

        # 读取日志文件内容
        content = await task_log_service.read_log(log_file_path, lines)
        log_info = await task_log_service.get_log_info(log_file_path)

        return success_response(
            LogFileResponse(
                execution_id=run_id,
                log_type=log_type,
                content=content,
                file_path=log_file_path,
                file_size=log_info.get("size", 0),
                lines_count=log_info.get("lines", 0),
                last_modified=log_info.get("modified_time"),
            ),
            message=Messages.QUERY_SUCCESS,
        )

    except DoesNotExist:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="执行记录不存在")


router = runs_router

__all__ = ["runs_router", "router"]
