# src/schemas/scheduler.py
"""调度器相关数据模式"""
from datetime import datetime
from typing import Optional, List, Dict, Any

from pydantic import BaseModel, Field, field_validator

from src.models.enums import TaskStatus, TaskType, ScheduleType


class TaskBase(BaseModel):
    """任务基础模型"""
    name: str = Field(..., min_length=3, max_length=255, description="任务名称")
    description: Optional[str] = Field(None, max_length=500)
    project_id: int = Field(..., description="关联项目ID")
    schedule_type: ScheduleType = Field(..., description="调度类型")
    is_active: bool = Field(True, description="是否激活")
class TaskCreate(TaskBase):
    """创建任务请求"""
    cron_expression: Optional[str] = Field(None, description="Cron表达式")
    interval_seconds: Optional[int] = Field(None, gt=0, description="间隔秒数")
    scheduled_time: Optional[datetime] = Field(None, description="计划执行时间")
    max_instances: int = Field(1, ge=1, le=10, description="最大并发实例数")
    timeout_seconds: int = Field(3600, gt=0, description="超时时间(秒)")
    retry_count: int = Field(3, ge=0, le=10, description="重试次数")
    retry_delay: int = Field(60, gt=0, description="重试延迟(秒)")
    execution_params: Optional[Dict[str, Any]] = Field(None, description="执行参数")
    environment_vars: Optional[Dict[str, str]] = Field(None, description="环境变量")

    @field_validator('cron_expression')
    def validate_cron(cls, v, info):
        values = info.data
        if values.get('schedule_type') == ScheduleType.CRON and not v:
            raise ValueError('Cron expression is required for cron schedule type')
        return v

    @field_validator('interval_seconds')
    def validate_interval(cls, v, info):
        values = info.data
        if values.get('schedule_type') == ScheduleType.INTERVAL and not v:
            raise ValueError('Interval seconds is required for interval schedule type')
        return v

    @field_validator('scheduled_time')
    def validate_scheduled_time(cls, v, info):
        values = info.data
        if values.get('schedule_type') == ScheduleType.DATE and not v:
            raise ValueError('Scheduled time is required for date schedule type')
        return v


class TaskUpdate(BaseModel):
    """更新任务请求"""
    name: Optional[str] = Field(None, min_length=3, max_length=255)
    description: Optional[str] = Field(None, max_length=500)
    is_active: Optional[bool] = None
    cron_expression: Optional[str] = None
    interval_seconds: Optional[int] = Field(None, gt=0)
    scheduled_time: Optional[datetime] = None
    max_instances: Optional[int] = Field(None, ge=1, le=10)
    timeout_seconds: Optional[int] = Field(None, gt=0)
    retry_count: Optional[int] = Field(None, ge=0, le=10)
    retry_delay: Optional[int] = Field(None, gt=0)
    execution_params: Optional[Dict[str, Any]] = None
    environment_vars: Optional[Dict[str, str]] = None


class TaskResponse(TaskBase):
    """任务响应模型"""
    id: int
    task_type: TaskType
    status: TaskStatus
    cron_expression: Optional[str]
    interval_seconds: Optional[int]
    scheduled_time: Optional[datetime]
    last_run_time: Optional[datetime]
    next_run_time: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    created_by: int = Field(description="创建者ID")
    created_by_username: Optional[str] = Field(None, description="创建者用户名")

    class Config:
        from_attributes = True


class ExecutionResponse(BaseModel):
    """执行记录响应"""
    id: int
    execution_id: str
    task_id: int
    start_time: datetime
    end_time: Optional[datetime]
    duration_seconds: Optional[float]
    status: TaskStatus
    exit_code: Optional[int]
    error_message: Optional[str]
    result_data: Optional[Dict[str, Any]]

    # 这些字段在模型中不存在，设为可选并提供默认值
    stdout: Optional[str] = None
    stderr: Optional[str] = None

    class Config:
        from_attributes = True


class ExecutionLogResponse(BaseModel):
    """执行日志响应"""
    id: int
    level: str
    message: str
    timestamp: datetime
    context: Optional[Dict[str, Any]]

    class Config:
        from_attributes = True


class LogFileResponse(BaseModel):
    """日志文件响应"""
    execution_id: str
    log_type: str  # "output" 或 "error"
    content: str
    file_path: str
    file_size: int
    lines_count: int
    last_modified: Optional[datetime] = None


class TaskStatsResponse(BaseModel):
    """任务统计响应"""
    total_executions: int
    success_count: int
    failed_count: int
    success_rate: float
    average_duration: float


class SystemMetricsResponse(BaseModel):
    """系统指标响应（扩展字节级信息，便于前端展示）"""
    cpu_percent: float
    cpu_cores: int | None = None
    memory_percent: float
    memory_total: int | None = None
    memory_used: int | None = None
    memory_available: int | None = None
    disk_usage: float
    disk_total: int | None = None
    disk_used: int | None = None
    disk_free: int | None = None
    active_tasks: int
    uptime_seconds: int | None = None


class TaskListResponse(BaseModel):
    """任务列表响应"""
    total: int
    page: int
    size: int
    items: List[TaskResponse]


class ExecutionListResponse(BaseModel):
    """执行记录列表响应"""
    total: int
    page: int
    size: int
    items: List[ExecutionResponse]
