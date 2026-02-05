# src/schemas/scheduler.py
"""调度器相关数据模式"""
from datetime import datetime
from typing import Optional, List, Dict, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.models.enums import TaskStatus, TaskType, ScheduleType, ExecutionStrategy


class TaskBase(BaseModel):
    """任务基础模型"""
    name: str = Field(..., min_length=3, max_length=255, description="任务名称")
    description: Optional[str] = Field(None, max_length=500)
    project_id: str = Field(..., description="关联项目公开ID")
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

    # 执行策略相关字段
    execution_strategy: Optional[ExecutionStrategy] = Field(
        None, 
        description="执行策略（为空则继承项目配置）: local-本地, fixed-固定节点, specified-指定节点, auto-自动选择, prefer-优先绑定"
    )
    specified_node_id: Optional[str] = Field(
        None, 
        description="指定执行节点ID（仅 specified 策略时使用）"
    )
    @field_validator("specified_node_id")
    def validate_specified_node_id(cls, v, info):
        values = info.data
        strategy = values.get("execution_strategy")
        if strategy == ExecutionStrategy.SPECIFIED and not v:
            raise ValueError("执行策略为 specified 时必须指定 specified_node_id")
        if v and strategy != ExecutionStrategy.SPECIFIED:
            raise ValueError("指定 specified_node_id 时必须将执行策略设置为 specified")
        return v

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

    # 执行策略相关字段
    execution_strategy: Optional[ExecutionStrategy] = Field(
        None, 
        description="执行策略（为空则继承项目配置）"
    )
    specified_node_id: Optional[str] = Field(
        None, 
        description="指定执行节点ID（仅 specified 策略时使用）"
    )


class TaskResponse(BaseModel):
    """任务响应模型"""
    id: str = Field(description="任务公开ID")
    name: str
    description: Optional[str] = None
    project_id: str = Field(description="关联项目公开ID")
    task_type: TaskType
    schedule_type: ScheduleType
    is_active: bool
    status: TaskStatus
    cron_expression: Optional[str] = None
    interval_seconds: Optional[int] = None
    scheduled_time: Optional[datetime] = None
    last_run_time: Optional[datetime] = None
    next_run_time: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    created_by: str = Field(description="创建者公开ID")
    created_by_username: Optional[str] = Field(None, description="创建者用户名")

    # 执行策略相关字段
    execution_strategy: Optional[str] = Field(None, description="执行策略")
    specified_node_id: Optional[str] = Field(None, description="指定执行节点ID")
    specified_node_name: Optional[str] = Field(None, description="指定执行节点名称")

    # 项目执行配置（继承自项目）
    project_execution_strategy: Optional[str] = Field(None, description="项目执行策略")
    project_bound_node_id: Optional[str] = Field(None, description="项目绑定节点ID")
    project_bound_node_name: Optional[str] = Field(None, description="项目绑定节点名称")

    model_config = ConfigDict(from_attributes=True)


class ExecutionResponse(BaseModel):
    """执行记录响应"""
    id: str = Field(description="执行记录公开ID")
    execution_id: str = Field(description="执行UUID")
    task_id: str = Field(description="任务公开ID")
    start_time: datetime
    end_time: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    status: TaskStatus
    exit_code: Optional[int] = None
    error_message: Optional[str] = None
    retry_count: int = Field(0, description="已重试次数")
    result_data: Optional[Dict[str, Any]] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_orm(cls, obj):
        """从 ORM 对象创建响应，使用 public_id"""
        return cls(
            id=obj.public_id,
            execution_id=obj.execution_id,
            task_id=getattr(obj, 'task_public_id', None),
            start_time=obj.start_time,
            end_time=obj.end_time,
            duration_seconds=obj.duration_seconds,
            status=obj.status,
            exit_code=obj.exit_code,
            error_message=obj.error_message,
            retry_count=getattr(obj, "retry_count", 0) or 0,
            result_data=obj.result_data,
            stdout=getattr(obj, 'stdout', None),
            stderr=getattr(obj, 'stderr', None)
        )


class ExecutionLogResponse(BaseModel):
    """执行日志响应"""
    id: str = Field(description="日志公开ID")
    level: str
    message: str
    timestamp: datetime
    context: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(from_attributes=True)


class LogFileResponse(BaseModel):
    """日志文件响应"""
    execution_id: str
    log_type: str
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
    """系统指标响应"""
    cpu_percent: float
    cpu_cores: int | None = None
    memory_percent: float
    memory_total: int | None = None
    memory_used: int | None = None
    memory_available: int | None = None
    disk_percent: float  # 统一命名：改为 disk_percent
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
