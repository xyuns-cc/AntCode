"""任务重试与补偿 API"""

from fastapi import APIRouter, Depends, HTTPException, status, Query, Body
from loguru import logger

from src.core.security.auth import TokenData, get_current_user
from src.core.response import success
from src.models import User
from src.services.scheduler.retry_service import retry_service

router = APIRouter(prefix="/retry", tags=["任务重试"])


@router.post(
    "/manual/{execution_id}",
    summary="手动重试任务",
    description="手动触发失败任务的重试"
)
async def manual_retry_task(
    execution_id: str,
    current_user: TokenData = Depends(get_current_user)
):
    """手动重试任务"""
    result = await retry_service.manual_retry(
        execution_id=execution_id,
        user_id=current_user.user_id
    )

    if not result["success"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result["error"]
        )

    return success(result, message="任务已触发重试")


@router.get(
    "/stats/{task_id}",
    summary="获取任务重试统计",
    description="获取指定任务的重试统计信息"
)
async def get_retry_stats(
    task_id: str,
    current_user: TokenData = Depends(get_current_user)
):
    """获取任务重试统计"""
    from src.models.scheduler import ScheduledTask

    # 支持 public_id
    task = await ScheduledTask.get_or_none(public_id=task_id)
    if not task:
        try:
            task = await ScheduledTask.get_or_none(id=int(task_id))
        except ValueError:
            pass

    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="任务不存在"
        )

    # 检查权限
    user = await User.get_or_none(id=current_user.user_id)
    if not user.is_admin and task.user_id != current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权访问此任务"
        )

    stats = await retry_service.get_retry_stats(task.id)
    stats["task_id"] = task.public_id
    return success(stats)


@router.get(
    "/pending",
    summary="获取待重试任务",
    description="获取当前待重试的任务列表（仅管理员）"
)
async def get_pending_retries(
    current_user: TokenData = Depends(get_current_user)
):
    """获取待重试任务列表"""
    from src.models.scheduler import ScheduledTask

    user = await User.get_or_none(id=current_user.user_id)
    if not user or not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限"
        )

    pending = await retry_service.get_pending_retries()

    # 将内部 task_id 转换为 public_id
    task_ids = [item["task_id"] for item in pending]
    tasks = await ScheduledTask.filter(id__in=task_ids).all()
    task_map = {task.id: task.public_id for task in tasks}

    for item in pending:
        item["task_id"] = task_map.get(item["task_id"], item["task_id"])

    return success({"items": pending, "total": len(pending)})


@router.post(
    "/config/{task_id}",
    summary="更新任务重试配置",
    description="更新指定任务的重试配置"
)
async def update_retry_config(
    task_id: str,
    max_retries: int = Body(3, ge=0, le=10, description="最大重试次数"),
    retry_delay: int = Body(60, ge=10, le=3600, description="重试延迟（秒）"),
    strategy: str = Body("exponential", description="重试策略"),
    current_user: TokenData = Depends(get_current_user)
):
    """更新任务重试配置"""
    from src.models.scheduler import ScheduledTask

    # 支持 public_id
    task = await ScheduledTask.get_or_none(public_id=task_id)
    if not task:
        try:
            task = await ScheduledTask.get_or_none(id=int(task_id))
        except ValueError:
            pass

    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="任务不存在"
        )

    # 检查权限
    user = await User.get_or_none(id=current_user.user_id)
    if not user.is_admin and task.user_id != current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权修改此任务"
        )

    # 更新配置
    task.retry_count = max_retries
    task.retry_delay = retry_delay
    await task.save()

    logger.info(f"任务 {task.name} 重试配置已更新: max_retries={max_retries}, delay={retry_delay}")

    return success({
        "task_id": task_id,
        "max_retries": max_retries,
        "retry_delay": retry_delay,
        "strategy": strategy
    }, message="重试配置已更新")


@router.post(
    "/cancel/{execution_id}",
    summary="取消待重试任务",
    description="取消队列中待重试的任务"
)
async def cancel_pending_retry(
    execution_id: str,
    current_user: TokenData = Depends(get_current_user)
):
    """取消待重试任务"""
    from src.models.scheduler import TaskExecution, ScheduledTask
    from src.models.enums import TaskStatus

    execution = await TaskExecution.get_or_none(execution_id=execution_id)
    if not execution:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="执行记录不存在"
        )

    task = await ScheduledTask.get_or_none(id=execution.task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="任务不存在"
        )

    # 检查权限
    user = await User.get_or_none(id=current_user.user_id)
    if not user.is_admin and task.user_id != current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权操作此任务"
        )

    # 更新状态为已取消
    execution.status = TaskStatus.CANCELLED
    execution.error_message = f"重试已取消 by user {current_user.user_id}"
    await execution.save()

    logger.info(f"任务 {task.name} 的重试已取消 by user {current_user.user_id}")

    return success({
        "execution_id": execution_id,
        "status": "cancelled"
    }, message="重试已取消")


@router.get(
    "/history/{task_id}",
    summary="获取任务重试历史",
    description="获取指定任务的重试历史记录"
)
async def get_retry_history(
    task_id: str,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    current_user: TokenData = Depends(get_current_user)
):
    """获取任务重试历史"""
    from src.models.scheduler import ScheduledTask, TaskExecution

    # 支持 public_id
    task = await ScheduledTask.get_or_none(public_id=task_id)
    if not task:
        try:
            task = await ScheduledTask.get_or_none(id=int(task_id))
        except ValueError:
            pass

    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="任务不存在"
        )

    # 检查权限
    user = await User.get_or_none(id=current_user.user_id)
    if not user.is_admin and task.user_id != current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权访问此任务"
        )

    # 查询有重试的执行记录
    query = TaskExecution.filter(task_id=task.id, retry_count__gt=0)
    total = await query.count()

    executions = await query.order_by("-start_time").offset((page - 1) * size).limit(size)

    items = [
        {
            "execution_id": e.execution_id,
            "public_id": e.public_id,
            "status": e.status.value,
            "retry_count": e.retry_count,
            "start_time": e.start_time.isoformat() if e.start_time else None,
            "end_time": e.end_time.isoformat() if e.end_time else None,
            "error_message": e.error_message
        }
        for e in executions
    ]

    return success({
        "items": items,
        "total": total,
        "page": page,
        "size": size
    })
