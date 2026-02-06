"""
任务定义模型

计划任务的数据模型定义。
"""

from tortoise import fields

from antcode_core.domain.models.base import BaseModel, generate_public_id
from antcode_core.domain.models.enums import (
    ExecutionStrategy,
    ScheduleType,
    TaskStatus,
    TaskType,
)


class Task(BaseModel):
    """计划任务模型

    表示一个计划任务定义，包含调度配置和执行参数。
    """

    public_id = fields.CharField(
        max_length=32, unique=True, default=generate_public_id, db_index=True
    )
    name = fields.CharField(max_length=255, unique=True)
    description = fields.TextField(null=True)

    project_id = fields.BigIntField()
    task_type = fields.CharEnumField(TaskType)

    # 调度配置
    schedule_type = fields.CharEnumField(ScheduleType)
    cron_expression = fields.CharField(max_length=100, null=True)
    interval_seconds = fields.IntField(null=True)
    scheduled_time = fields.DatetimeField(null=True)

    # 执行配置
    max_instances = fields.IntField(default=1)
    timeout_seconds = fields.IntField(default=3600)
    retry_count = fields.IntField(default=3)
    retry_delay = fields.IntField(default=60)

    # 状态
    status = fields.CharEnumField(TaskStatus, default=TaskStatus.PENDING)
    is_active = fields.BooleanField(default=True)
    last_run_time = fields.DatetimeField(null=True)
    next_run_time = fields.DatetimeField(null=True)
    failure_count = fields.IntField(default=0)
    success_count = fields.IntField(default=0)

    # 执行参数
    execution_params = fields.JSONField(null=True)
    environment_vars = fields.JSONField(null=True)

    # 时间戳
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)
    user_id = fields.BigIntField()

    # ========== 执行策略相关字段 ==========
    # 执行策略（可覆盖项目配置，为空则继承项目）
    execution_strategy = fields.CharEnumField(
        ExecutionStrategy, null=True, description="执行策略（为空则继承项目配置）"
    )
    # 指定执行 Worker ID（仅 specified 策略时使用）
    specified_worker_id = fields.BigIntField(
        null=True,
        db_index=True,
        description="指定执行 Worker ID",
    )

    class Meta:
        table = "scheduled_tasks"
        indexes = [
            ("name",),
            ("status",),
            ("is_active",),
            ("user_id",),
            ("project_id",),
            ("created_at",),
            ("next_run_time",),
            ("task_type",),
            ("is_active", "status", "next_run_time"),
            ("status", "created_at"),
            ("user_id", "status"),
            ("project_id", "status"),
            ("task_type", "status"),
            ("public_id",),
            ("execution_strategy",),
            ("specified_worker_id",),
        ]

    def __str__(self):
        return f"{self.name} ({self.status})"


__all__ = [
    "Task",
]
