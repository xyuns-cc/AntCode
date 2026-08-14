"""任务重试与补偿 API"""

import contextlib
from datetime import UTC, datetime
from typing import Any

from antcode_core.application.services.scheduler.retry_configuration_service import (
    RetryConfigurationConflictError,
    apply_retry_configuration,
)
from antcode_core.application.services.scheduler.retry_service import retry_service
from antcode_core.common.error_messages import normalize_persisted_error_message
from antcode_core.common.security.auth import TokenData, get_current_user
from antcode_core.domain.models import User, UserRole
from antcode_core.domain.schemas.common import BaseResponse
from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from loguru import logger

from antcode_web_api.deps import require_role
from antcode_web_api.response import success
from antcode_web_api.routes.v1.retry_config import RetryConfigUpdate

router = APIRouter()


@router.post(
    "/manual/{run_id}",
    response_model=BaseResponse[dict[str, Any]],
    summary="手动重试任务",
    description="手动触发失败任务的重试",
)
async def manual_retry_task(run_id: str, current_user: TokenData = Depends(get_current_user)):
    """手动重试任务"""
    # D2: owner 校验，与本文件其它端点（stats/history/cancel）保持一致；此前遗漏
    # 让任意登录用户可 POST /manual/{任意 run_id} 重跑他人任务。
    from antcode_core.domain.models.task import Task
    from antcode_core.domain.models.task_run import TaskRun

    execution = await TaskRun.get_or_none(run_id=run_id)
    if not execution:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="执行记录不存在")
    task = await Task.get_or_none(id=execution.task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="关联任务不存在")
    user = await User.get_or_none(id=current_user.user_id)
    if not user or (not user.is_admin and task.user_id != current_user.user_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问此任务")

    result = await retry_service.manual_retry(run_id=run_id, user_id=current_user.user_id)

    if not result["success"]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result["error"])

    return success(result, message="任务已触发重试")


@router.get(
    "/stats/{task_id}",
    response_model=BaseResponse[dict[str, Any]],
    summary="获取任务重试统计",
    description="获取指定任务的重试统计信息",
)
async def get_retry_stats(task_id: str, current_user: TokenData = Depends(get_current_user)):
    """获取任务重试统计"""
    from antcode_core.domain.models.task import Task

    # 支持 public_id
    task = await Task.get_or_none(public_id=task_id)
    if not task:
        with contextlib.suppress(ValueError):
            task = await Task.get_or_none(id=int(task_id))

    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")

    # 检查权限
    user = await User.get_or_none(id=current_user.user_id)
    # P1-09: token 里的 user_id 已被删除时,返回 401 而不是 AttributeError → 500
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在或已被删除",
        )
    if not user.is_admin and task.user_id != current_user.user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问此任务")

    stats = await retry_service.get_retry_stats(task.id)
    stats["task_id"] = task.public_id
    return success(stats)


@router.get(
    "/pending",
    response_model=BaseResponse[dict[str, Any]],
    summary="获取待重试任务",
    description="获取当前待重试的任务列表（仅管理员）",
)
async def get_pending_retries(
    _admin: User = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN)),
):
    """获取待重试任务列表"""
    from antcode_core.domain.models.task import Task

    pending = await retry_service.get_pending_retries()

    if not pending:
        return success({"items": [], "total": 0})

    # 将内部 task_id 转换为 public_id
    task_ids = [item["task_id"] for item in pending]
    tasks = await Task.filter(id__in=task_ids).all()
    task_map = {task.id: task.public_id for task in tasks}

    for item in pending:
        item["task_id"] = task_map.get(item["task_id"], item["task_id"])

    return success({"items": pending, "total": len(pending)})


@router.post(
    "/config/{task_id}",
    response_model=BaseResponse[dict[str, Any]],
    summary="更新任务重试配置",
    description="更新指定任务的重试配置",
)
async def update_retry_config(
    task_id: str,
    config: RetryConfigUpdate = Body(...),
    current_user: TokenData = Depends(get_current_user),
):
    """更新任务重试配置"""
    from antcode_core.domain.models.task import Task

    # 支持 public_id
    task = await Task.get_or_none(public_id=task_id)
    if not task:
        with contextlib.suppress(ValueError):
            task = await Task.get_or_none(id=int(task_id))

    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")

    # 检查权限
    user = await User.get_or_none(id=current_user.user_id)
    # P1-09: token 里的 user_id 已被删除时,返回 401 而不是 AttributeError → 500
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在或已被删除",
        )
    if not user.is_admin and task.user_id != current_user.user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权修改此任务")

    changes = config.database_changes()
    if changes:
        changes["updated_at"] = datetime.now(UTC)
    try:
        cancelled_runs = await apply_retry_configuration(
            task.id,
            changes,
            user_id=current_user.user_id,
        )
    except RetryConfigurationConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    for run_id in cancelled_runs:
        await retry_service.cancel_pending(run_id)

    refreshed = await Task.get_or_none(id=task.id)
    if not refreshed:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="任务已被并发删除")
    logger.info(
        f"任务 {refreshed.name} 重试配置已更新: max_retries={refreshed.retry_count}, delay={refreshed.retry_delay}"
    )

    return success(
        {
            "task_id": refreshed.public_id,
            "max_retries": refreshed.retry_count,
            "retry_delay": refreshed.retry_delay,
            "strategy": config.strategy or "exponential",
        },
        message="重试配置已更新",
    )


@router.post(
    "/cancel/{run_id}",
    response_model=BaseResponse[dict[str, Any]],
    summary="取消待重试任务",
    description="取消队列中待重试的任务",
)
async def cancel_pending_retry(run_id: str, current_user: TokenData = Depends(get_current_user)):
    """取消待重试任务。

    P1-FN-01 修复：此前只改 TaskRun.status，Redis pending 意图和 DB
    ``next_retry_at`` 都保留 —— Master 会照常 claim 并创建新 run，取消
    实际无效。现在按顺序：
    1. 清 DB durable intent（``next_retry_at=None``）并置终态 —— Master
       的 ``_validate_retry_source`` / ``_recover_from_db`` 都以此为准，
       之后任何在途 claim 都会被判定 intent 失效丢弃；
    2. 再移除 Redis pending 条目（尽力而为，失败也不影响正确性）。
    """
    from antcode_core.domain.models.task import Task
    from antcode_core.domain.models.task_run import TaskRun

    execution = await TaskRun.get_or_none(run_id=run_id)
    if not execution:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="执行记录不存在")

    task = await Task.get_or_none(id=execution.task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")

    # 检查权限
    user = await User.get_or_none(id=current_user.user_id)
    # P1-09: token 里的 user_id 已被删除时,返回 401 而不是 AttributeError → 500
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在或已被删除",
        )
    if not user.is_admin and task.user_id != current_user.user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权操作此任务")

    from antcode_core.application.services.scheduler.retry_cancellation_service import (
        RetryIntentNotPendingError,
        cancel_retry_intent,
    )

    try:
        await cancel_retry_intent(run_id, user_id=current_user.user_id)
    except RetryIntentNotPendingError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    removed = await retry_service.cancel_pending(run_id)

    logger.info(f"任务 {task.name} 的重试已取消 by user {current_user.user_id} (redis_removed={removed})")

    return success({"run_id": run_id, "status": "cancelled"}, message="重试已取消")


@router.get(
    "/history/{task_id}",
    response_model=BaseResponse[dict[str, Any]],
    summary="获取任务重试历史",
    description="获取指定任务的重试历史记录",
)
async def get_retry_history(
    task_id: str,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    current_user: TokenData = Depends(get_current_user),
):
    """获取任务重试历史"""
    from antcode_core.domain.models.task import Task
    from antcode_core.domain.models.task_run import TaskRun

    # 支持 public_id
    task = await Task.get_or_none(public_id=task_id)
    if not task:
        with contextlib.suppress(ValueError):
            task = await Task.get_or_none(id=int(task_id))

    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")

    # 检查权限
    user = await User.get_or_none(id=current_user.user_id)
    # P1-09: token 里的 user_id 已被删除时,返回 401 而不是 AttributeError → 500
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在或已被删除",
        )
    if not user.is_admin and task.user_id != current_user.user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问此任务")

    # 查询有重试的执行记录
    query = TaskRun.filter(task_id=task.id, retry_count__gt=0)
    total = await query.count()

    executions = await query.order_by("-start_time").offset((page - 1) * size).limit(size)

    items = [
        {
            "run_id": e.run_id,
            "public_id": e.public_id,
            "status": e.status.value,
            "retry_count": e.retry_count,
            "start_time": e.start_time.isoformat() if e.start_time else None,
            "end_time": e.end_time.isoformat() if e.end_time else None,
            "error_message": normalize_persisted_error_message(e.error_message),
        }
        for e in executions
    ]

    return success({"items": items, "total": total, "page": page, "size": size})
