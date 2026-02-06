"""
任务 Schema

任务相关的请求和响应模式。
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from antcode_core.domain.models.enums import (
    DispatchStatus,
    ExecutionStrategy,
    ScheduleType,
    TaskStatus,
    TaskType,
)


class TaskCreateRequest(BaseModel):
    """任务创建请求"""
    name: str = Field(..., min_length=3, max_length=255, description="任务名称")
    description: str | None = Field(None, max_length=500)
    project_id: str = Field(..., description="关联项目公开ID")
    schedule_type: ScheduleType = Field(..., description="调度类型")
    is_active: bool = Field(True, description="是否激活")

    cron_expression: str | None = Field(None, description="Cron表达式")
    interval_seconds: int | None = Field(None, gt=0, description="间隔秒数")
    scheduled_time: datetime | None = Field(None, description="计划执行时间")
    max_instances: int = Field(1, ge=1, le=10, description="最大并发实例数")
    timeout_seconds: int = Field(3600, gt=0, description="超时时间(秒)")
    retry_count: int = Field(3, ge=0, le=10, description="重试次数")
    retry_delay: int = Field(60, gt=0, description="重试延迟(秒)")
    execution_params: dict[str, Any] | None = Field(None, description="执行参数")
    environment_vars: dict[str, str] | None = Field(None, description="环境变量")

    execution_strategy: ExecutionStrategy | None = Field(
        None, description="执行策略"
    )
    specified_worker_id: str | None = Field(
        None, description="指定执行 Worker ID"
    )

    @field_validator("cron_expression")
    @classmethod
    def validate_cron(cls, v, info):
        values = info.data
        if values.get("schedule_type") == ScheduleType.CRON and not v:
            raise ValueError("Cron expression is required for cron schedule type")
        return v

    @field_validator("interval_seconds")
    @classmethod
    def validate_interval(cls, v, info):
        values = info.data
        if values.get("schedule_type") == ScheduleType.INTERVAL and not v:
            raise ValueError("Interval seconds is required for interval schedule type")
        return v

    @field_validator("scheduled_time")
    @classmethod
    def validate_scheduled_time(cls, v, info):
        values = info.data
        if values.get("schedule_type") == ScheduleType.DATE and not v:
            raise ValueError("Scheduled time is required for date schedule type")
        return v


class TaskUpdateRequest(BaseModel):
    """任务更新请求"""
    name: str | None = Field(None, min_length=3, max_length=255)
    description: str | None = Field(None, max_length=500)
    is_active: bool | None = None
    cron_expression: str | None = None
    interval_seconds: int | None = Field(None, gt=0)
    scheduled_time: datetime | None = None
    max_instances: int | None = Field(None, ge=1, le=10)
    timeout_seconds: int | None = Field(None, gt=0)
    retry_count: int | None = Field(None, ge=0, le=10)
    retry_delay: int | None = Field(None, gt=0)
    execution_params: dict[str, Any] | None = None
    environment_vars: dict[str, str] | None = None

    execution_strategy: ExecutionStrategy | None = Field(None)
    specified_worker_id: str | None = Field(None)


class TaskResponse(BaseModel):
    """任务响应"""
    id: str = Field(description="任务公开ID")
    name: str
    description: str = ""
    project_id: str = Field(description="关联项目公开ID")
    task_type: TaskType
    schedule_type: ScheduleType
    is_active: bool
    status: TaskStatus
    cron_expression: str = ""
    interval_seconds: int = 0
    scheduled_time: str = ""
    last_run_time: str = ""
    next_run_time: str = ""
    created_at: datetime
    updated_at: datetime
    created_by: str = Field(description="创建者公开ID")
    created_by_username: str = Field("", description="创建者用户名")

    execution_strategy: str = Field("", description="执行策略")
    specified_worker_id: str = Field("", description="指定执行 Worker ID")
    specified_worker_name: str = Field("", description="指定执行 Worker 名称")
    project_execution_strategy: str = Field("", description="项目执行策略")
    project_bound_worker_id: str = Field("", description="项目绑定 Worker ID")
    project_bound_worker_name: str = Field("", description="项目绑定 Worker 名称")

    model_config = ConfigDict(from_attributes=True)


class TaskListResponse(BaseModel):
    """任务列表响应"""
    total: int
    page: int
    size: int
    items: list[TaskResponse]


class TaskRunResponse(BaseModel):
    """任务执行记录响应"""
    id: str = Field(description="执行记录公开ID")
    execution_id: str = Field(description="执行UUID")
    task_id: str = Field(description="任务公开ID")
    start_time: str = ""
    end_time: str = ""
    duration_seconds: float = 0.0
    status: TaskStatus
    dispatch_status: DispatchStatus
    runtime_status: str = ""
    dispatch_updated_at: str = ""
    runtime_updated_at: str = ""
    exit_code: int = 0
    error_message: str = ""
    result_data: dict[str, Any] = Field(default_factory=dict)
    stdout: str = ""
    stderr: str = ""
    worker_id: str = ""

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_orm(cls, obj):
        """从 ORM 对象创建响应，使用 public_id"""
        return cls(
            id=obj.public_id,
            execution_id=obj.execution_id,
            task_id=getattr(obj, "task_public_id", "") or "",
            start_time=obj.start_time.isoformat() if obj.start_time else "",
            end_time=obj.end_time.isoformat() if obj.end_time else "",
            duration_seconds=obj.duration_seconds or 0.0,
            status=obj.status,
            dispatch_status=obj.dispatch_status,
            runtime_status=obj.runtime_status.value if obj.runtime_status else "",
            dispatch_updated_at=obj.dispatch_updated_at.isoformat() if obj.dispatch_updated_at else "",
            runtime_updated_at=obj.runtime_updated_at.isoformat() if obj.runtime_updated_at else "",
            exit_code=obj.exit_code or 0,
            error_message=obj.error_message or "",
            result_data=obj.result_data or {},
            stdout=getattr(obj, "stdout", "") or "",
            stderr=getattr(obj, "stderr", "") or "",
            worker_id=str(obj.worker_public_id) if getattr(obj, "worker_public_id", None) else "",
        )


class TaskRunListResponse(BaseModel):
    """任务执行记录列表响应"""
    total: int
    page: int
    size: int
    items: list[TaskRunResponse]


class TaskStatsResponse(BaseModel):
    """任务统计响应"""
    total_executions: int
    success_count: int
    failed_count: int
    success_rate: float
    average_duration: float


class SystemMetricsResponse(BaseModel):
    """系统指标响应"""
    cpu_percent: float
    cpu_cores: int = 0
    memory_percent: float
    memory_total: int = 0
    memory_used: int = 0
    memory_available: int = 0
    disk_percent: float
    disk_total: int = 0
    disk_used: int = 0
    disk_free: int = 0
    active_tasks: int
    uptime_seconds: int = 0
    queue_size: int = 0
    success_rate: float = 0.0


__all__ = [
    "TaskCreateRequest",
    "TaskUpdateRequest",
    "TaskResponse",
    "TaskListResponse",
    "TaskRunResponse",
    "TaskRunListResponse",
    "TaskStatsResponse",
    "SystemMetricsResponse",
]
