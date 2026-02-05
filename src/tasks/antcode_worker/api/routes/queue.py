"""队列管理 API - 与主控 API 风格保持一致

提供:
- 批量任务接收
- 队列状态查询
- 优先级更新
"""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from loguru import logger

from ...core import (
    Scheduler,
    BatchReceiver,
    TaskItem,
    BatchTaskRequest,
    ProjectType,
)

router = APIRouter(prefix="/queue", tags=["队列管理"])


# ============ 请求模型 ============

class TaskItemRequest(BaseModel):
    task_id: str = Field(..., description="任务ID")
    project_id: str = Field(..., description="项目ID")
    project_type: str = Field(..., description="项目类型: file, code, rule")
    priority: Optional[int] = Field(None, description="优先级 (0-4)，不指定则使用默认")
    params: Dict[str, Any] = Field(default_factory=dict, description="任务参数")
    environment: Dict[str, str] = Field(default_factory=dict, description="环境变量")
    timeout: int = Field(3600, description="超时时间（秒）")
    download_url: Optional[str] = Field(None, description="下载URL")
    access_token: Optional[str] = Field(None, description="访问令牌")
    file_hash: Optional[str] = Field(None, description="文件哈希")
    entry_point: Optional[str] = Field(None, description="入口文件")


class BatchTaskRequestModel(BaseModel):
    tasks: List[TaskItemRequest] = Field(..., description="任务列表")
    node_id: str = Field(..., description="节点ID")
    batch_id: Optional[str] = Field(None, description="批次ID")


class PriorityUpdateRequest(BaseModel):
    priority: int = Field(..., description="新优先级 (0-4)")


# ============ 响应模型 ============

class BatchReceiveResponse(BaseModel):
    """批量接收响应"""
    batch_id: str
    received: int
    queued: int


class QueueStatusResponse(BaseModel):
    """队列状态响应"""
    total: int
    pending: int
    running: int
    by_priority: Dict[str, int]


class TaskDetailResponse(BaseModel):
    """任务详情响应"""
    task_id: str
    project_id: str
    project_type: str
    priority: int
    status: str
    queued_at: Optional[str] = None


class QueueDetailsResponse(BaseModel):
    """队列详情响应"""
    tasks: List[Dict[str, Any]]
    total: int


class PriorityUpdateResponse(BaseModel):
    """优先级更新响应"""
    task_id: str
    new_priority: int
    new_position: int


# ============ 全局状态 ============

_scheduler: Optional[Scheduler] = None
_batch_receiver: Optional[BatchReceiver] = None


def get_scheduler() -> Scheduler:
    """获取调度器（使用引擎的调度器）"""
    global _scheduler
    if _scheduler is None:
        # 尝试从引擎获取调度器
        from ..deps import get_engine
        engine = get_engine()
        if engine:
            _scheduler = engine.scheduler
        else:
            # 回退：创建独立调度器（不推荐）
            _scheduler = Scheduler()
            logger.warning("使用独立调度器，任务可能不会被执行")
    return _scheduler


def get_receiver() -> BatchReceiver:
    """获取批量任务接收器"""
    global _batch_receiver
    if _batch_receiver is None:
        _batch_receiver = BatchReceiver(get_scheduler())
    return _batch_receiver


def set_scheduler(scheduler: Scheduler) -> None:
    """设置调度器（由引擎初始化时调用）"""
    global _scheduler, _batch_receiver
    _scheduler = scheduler
    _batch_receiver = BatchReceiver(_scheduler)
    logger.info("队列组件已绑定到引擎调度器")


async def init_queue_components(persist_path: Optional[str] = None) -> None:
    """初始化队列组件（使用引擎的调度器）"""
    global _scheduler, _batch_receiver
    # 从引擎获取调度器
    from ..deps import get_engine
    engine = get_engine()
    if engine:
        _scheduler = engine.scheduler
        _batch_receiver = BatchReceiver(_scheduler)
        logger.info("队列组件已绑定到引擎调度器")
    else:
        # 回退：创建独立调度器
        _scheduler = Scheduler(persist_path=persist_path)
        await _scheduler.start()
        _batch_receiver = BatchReceiver(_scheduler)
        logger.warning("引擎未初始化，使用独立调度器")


async def shutdown_queue_components() -> None:
    """关闭队列组件"""
    global _scheduler
    # 如果使用的是引擎的调度器，不需要单独关闭
    # 引擎会负责关闭它的调度器
    pass


# ============ 路由 ============

@router.post("/batch", response_model=BatchReceiveResponse, status_code=status.HTTP_202_ACCEPTED)
async def receive_batch_tasks(request: BatchTaskRequestModel):
    """
    批量接收任务
    
    Master 调用此接口批量下发任务到节点
    """
    receiver = get_receiver()

    # 转换请求模型
    tasks = []
    for t in request.tasks:
        try:
            project_type = ProjectType(t.project_type)
        except ValueError:
            project_type = ProjectType.CODE

        tasks.append(TaskItem(
            task_id=t.task_id,
            project_id=t.project_id,
            project_type=project_type,
            priority=t.priority,
            params=t.params,
            environment=t.environment,
            timeout=t.timeout,
            download_url=t.download_url,
            access_token=t.access_token,
            file_hash=t.file_hash,
            entry_point=t.entry_point,
        ))

    batch_request = BatchTaskRequest(
        tasks=tasks,
        node_id=request.node_id,
        batch_id=request.batch_id or "",
    )

    response = await receiver.receive_batch(batch_request)

    return BatchReceiveResponse(
        batch_id=response.batch_id,
        received=response.accepted_count + response.rejected_count,
        queued=response.accepted_count,
    )


@router.get("/status", response_model=QueueStatusResponse)
async def get_queue_status():
    """获取队列状态"""
    scheduler = get_scheduler()
    queue_status = scheduler.get_status()

    # 从引擎获取运行中的任务数
    from ..deps import get_engine
    engine = get_engine()
    running_count = engine.running_count if engine else 0

    return QueueStatusResponse(
        total=queue_status.total_count + running_count,
        pending=queue_status.total_count,
        running=running_count,
        by_priority=queue_status.by_priority,
    )


@router.get("/details", response_model=QueueDetailsResponse)
async def get_queue_details():
    """获取队列详情（按优先级排序）"""
    scheduler = get_scheduler()
    details = scheduler.get_details()
    return QueueDetailsResponse(tasks=details, total=len(details))


@router.get("/stats")
async def get_queue_stats():
    """获取队列统计"""
    scheduler = get_scheduler()
    return scheduler.get_stats()


@router.put("/tasks/{task_id}/priority", response_model=PriorityUpdateResponse)
async def update_task_priority(task_id: str, request: PriorityUpdateRequest):
    """更新任务优先级"""
    scheduler = get_scheduler()

    if not (0 <= request.priority <= 4):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="优先级必须在 0-4 之间"
        )

    position = await scheduler.update_priority(task_id, request.priority)

    if position is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="任务不存在"
        )

    return PriorityUpdateResponse(
        task_id=task_id,
        new_priority=request.priority,
        new_position=position,
    )


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_task(task_id: str):
    """取消任务"""
    scheduler = get_scheduler()

    success = await scheduler.cancel(task_id)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="任务不存在"
        )

    return None
