"""任务管理 API - 与主控 API 风格保持一致

提供任务的创建、查询、取消等功能。
"""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from ..deps import get_engine
from ...core import TaskPriority, ProjectType

router = APIRouter(prefix="/tasks", tags=["任务管理"])


# ============ 请求模型 ============

class TaskCreateRequest(BaseModel):
    project_id: str = Field(..., description="项目ID")
    params: Dict[str, Any] = Field(default_factory=dict, description="任务参数")
    environment: Dict[str, str] = Field(default_factory=dict, description="环境变量")
    timeout: int = Field(3600, description="超时时间（秒）")
    priority: int = Field(TaskPriority.NORMAL.value, description="优先级 (0-4)")
    project_type: str = Field("code", description="项目类型")


# ============ 响应模型 ============

class TaskInfo(BaseModel):
    """任务信息"""
    task_id: str
    project_id: str
    status: str
    priority: int
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


class TaskListResponse(BaseModel):
    """任务列表响应"""
    tasks: List[Dict[str, Any]]
    total: int


class TaskCreateResponse(BaseModel):
    """任务创建响应"""
    task_id: str
    project_id: str
    status: str


# ============ 路由 ============

@router.post("", response_model=TaskCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_task(request: TaskCreateRequest):
    """创建任务"""
    engine = get_engine()

    try:
        project_type = ProjectType(request.project_type)
    except ValueError:
        project_type = ProjectType.CODE

    result = await engine.create_task(
        project_id=request.project_id,
        params=request.params,
        environment_vars=request.environment,
        timeout=request.timeout,
        priority=request.priority,
        project_type=project_type,
    )

    return TaskCreateResponse(
        task_id=result.get("task_id", ""),
        project_id=request.project_id,
        status=result.get("status", "pending"),
    )


@router.get("", response_model=TaskListResponse)
async def list_tasks(
    status_filter: Optional[str] = Query(None, alias="status", description="状态过滤"),
    limit: int = Query(50, description="限制数量"),
):
    """列出任务"""
    engine = get_engine()
    tasks = await engine.list_tasks(status=status_filter, limit=limit)
    return TaskListResponse(tasks=tasks, total=len(tasks))


@router.get("/stats")
async def get_task_stats():
    """获取任务统计"""
    engine = get_engine()
    return engine.get_stats()


@router.get("/{task_id}")
async def get_task(task_id: str):
    """获取任务详情"""
    engine = get_engine()
    task = await engine.get_task(task_id)

    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="任务不存在"
        )

    return task


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_task(task_id: str):
    """取消任务"""
    engine = get_engine()
    success = await engine.cancel_task(task_id)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="任务不存在或已完成"
        )

    return None
