"""
任务 Schema

任务相关的请求和响应模式。
"""

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from antcode_core.domain.models.enums import (
    DispatchStatus,
    ExecutionStrategy,
    ScheduleType,
    TaskStatus,
    TaskType,
)

# P1-29 JSON 字段边界:64KB 已经能兜住任何合理的执行参数/环境变量,
# 超过就 422,防止攻击者塞进 MB 级 dict 打爆 DB / event loop。
_MAX_JSON_FIELD_BYTES = 64 * 1024


def _assert_json_payload_within(value: dict[str, Any] | None, field: str) -> None:
    """确保 JSONField 序列化后 <= _MAX_JSON_FIELD_BYTES,超过直接 ValueError。"""
    if value is None:
        return
    try:
        # default=str 兜住 datetime 等非 JSON 原生类型,避免这里抛出把
        # 422 消息带偏成 TypeError。
        encoded = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{field} 不是可序列化的 JSON: {e}") from e
    size = len(encoded.encode("utf-8"))
    if size > _MAX_JSON_FIELD_BYTES:
        raise ValueError(
            f"{field} 序列化后为 {size} 字节,超过上限 {_MAX_JSON_FIELD_BYTES} 字节"
        )


class TaskCreateRequest(BaseModel):
    """任务创建请求"""

    name: str = Field(..., min_length=3, max_length=255, description="任务名称")
    description: str | None = Field(None, max_length=500)
    project_id: str = Field(..., description="关联项目公开ID")
    schedule_type: ScheduleType = Field(..., description="调度类型")
    is_active: bool = Field(True, description="是否激活")

    # cron_expression 对齐 DB 列 CharField(max_length=100),避免 10MB
    # cron 触发 asyncpg StringDataRightTruncation 500(P1-29)。
    cron_expression: str | None = Field(
        None, max_length=100, description="Cron表达式(<= 100 字符)"
    )
    interval_seconds: int | None = Field(None, gt=0, description="间隔秒数")
    scheduled_time: datetime | None = Field(None, description="计划执行时间")
    max_instances: int = Field(1, ge=1, le=10, description="最大并发实例数")
    timeout_seconds: int = Field(3600, gt=0, description="超时时间(秒)")
    retry_count: int = Field(3, ge=0, le=10, description="重试次数")
    retry_delay: int = Field(60, gt=0, description="重试延迟(秒)")
    execution_params: dict[str, Any] | None = Field(None, description="执行参数(JSON <= 64KB)")
    environment_vars: dict[str, str] | None = Field(None, description="环境变量(JSON <= 64KB)")

    execution_strategy: ExecutionStrategy | None = Field(None, description="执行策略")
    specified_worker_id: str | None = Field(None, description="指定执行 Worker ID")

    @model_validator(mode="after")
    def _validate_trigger_fields(self):
        """跨字段校验:根据 schedule_type 强制要求对应触发器字段,并对 cron 语法做即时试构造。

        用 field_validator 时,如果字段本身缺失(比如请求根本没带 cron_expression),
        对应 validator 不会执行,导致非法请求可以落库。改成 model_validator(after)
        确保无论字段是否出现都能触发校验。
        """
        if self.schedule_type == ScheduleType.CRON:
            if not self.cron_expression:
                raise ValueError("CRON 任务必须提供 cron_expression")
            # 立刻用 APScheduler 试构造一次 CronTrigger 验证语法,
            # 让非法 cron 在 schema 层就 422,而不是等到落库后 add_job 才 400。
            try:
                from apscheduler.triggers.cron import CronTrigger

                CronTrigger.from_crontab(self.cron_expression)
            except Exception as e:  # noqa: BLE001
                raise ValueError(f"非法 cron 表达式: {e}") from e
        elif self.schedule_type == ScheduleType.INTERVAL:
            if not self.interval_seconds:
                raise ValueError("INTERVAL 任务必须提供 interval_seconds")
        elif self.schedule_type == ScheduleType.DATE:
            if not self.scheduled_time:
                raise ValueError("DATE 任务必须提供 scheduled_time")

        # P1-29 JSON 字段边界:cap execution_params / environment_vars 序列化后总量
        _assert_json_payload_within(self.execution_params, "execution_params")
        _assert_json_payload_within(self.environment_vars, "environment_vars")
        return self


class TaskUpdateRequest(BaseModel):
    """任务更新请求"""

    name: str | None = Field(None, min_length=3, max_length=255)
    description: str | None = Field(None, max_length=500)
    is_active: bool | None = None
    # 对齐 DB 列 max_length=100,防止超长 cron 触发 asyncpg 截断 500(P1-29)。
    cron_expression: str | None = Field(None, max_length=100)
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

    @model_validator(mode="after")
    def _validate_cron_syntax(self):
        """更新时若传了 cron_expression,也立刻用 CronTrigger 试构造一次,
        避免非法 cron 落到 DB 后 reschedule_job 才炸出 500。"""
        if self.cron_expression:
            try:
                from apscheduler.triggers.cron import CronTrigger

                CronTrigger.from_crontab(self.cron_expression)
            except Exception as e:  # noqa: BLE001
                raise ValueError(f"非法 cron 表达式: {e}") from e

        # P1-29 JSON 字段边界:同 create,防止 update 通道绕过大小 cap。
        _assert_json_payload_within(self.execution_params, "execution_params")
        _assert_json_payload_within(self.environment_vars, "environment_vars")
        return self


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

    runtime_kind: str = Field("", description="运行时类型")
    runtime_scope: str = Field("", description="运行时作用域")
    python_version: str = Field("", description="Python 版本")
    runtime_locator: str = Field("", description="运行时定位符")

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
    run_id: str = Field(description="运行UUID")
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
            run_id=obj.run_id,
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
