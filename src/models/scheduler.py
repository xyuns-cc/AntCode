"""任务调度模型"""
from tortoise import fields

from src.models.base import BaseModel, generate_public_id
from src.models.enums import TaskStatus, TaskType, ScheduleType, ExecutionStrategy


class ScheduledTask(BaseModel):
    """计划任务模型"""
    public_id = fields.CharField(max_length=32, unique=True, default=generate_public_id, db_index=True)
    name = fields.CharField(max_length=255, unique=True)
    description = fields.TextField(null=True)

    project_id = fields.BigIntField()
    task_type = fields.CharEnumField(TaskType)

    schedule_type = fields.CharEnumField(ScheduleType)
    cron_expression = fields.CharField(max_length=100, null=True)
    interval_seconds = fields.IntField(null=True)
    scheduled_time = fields.DatetimeField(null=True)

    max_instances = fields.IntField(default=1)
    timeout_seconds = fields.IntField(default=3600)
    retry_count = fields.IntField(default=3)
    retry_delay = fields.IntField(default=60)

    status = fields.CharEnumField(TaskStatus, default=TaskStatus.PENDING)
    is_active = fields.BooleanField(default=True)
    last_run_time = fields.DatetimeField(null=True)
    next_run_time = fields.DatetimeField(null=True)
    failure_count = fields.IntField(default=0)
    success_count = fields.IntField(default=0)

    execution_params = fields.JSONField(null=True)
    environment_vars = fields.JSONField(null=True)

    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)
    user_id = fields.BigIntField()

    # ========== 执行策略相关字段 ==========
    # 执行策略（可覆盖项目配置，为空则继承项目）
    execution_strategy = fields.CharEnumField(
        ExecutionStrategy, 
        null=True,
        description="执行策略（为空则继承项目配置）"
    )
    # 指定执行节点ID（仅 specified 策略时使用）
    specified_node_id = fields.BigIntField(null=True, db_index=True, description="指定执行节点ID")

    # 兼容旧字段（已废弃，保留用于迁移）
    node_id = fields.BigIntField(null=True, db_index=True, description="[已废弃] 所属节点ID")

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
            ("node_id",),
            ("node_id", "status"),
            ("node_id", "user_id"),
            ("execution_strategy",),
            ("specified_node_id",),
        ]

    def __str__(self):
        return f"{self.name} ({self.status})"


class TaskExecution(BaseModel):
    """任务执行记录"""
    public_id = fields.CharField(max_length=32, unique=True, default=generate_public_id, db_index=True)
    task_id = fields.BigIntField()

    execution_id = fields.CharField(max_length=64, unique=True)
    start_time = fields.DatetimeField()
    end_time = fields.DatetimeField(null=True)
    duration_seconds = fields.FloatField(null=True)

    status = fields.CharEnumField(TaskStatus)
    exit_code = fields.IntField(null=True)
    error_message = fields.TextField(null=True)
    retry_count = fields.IntField(default=0)

    log_file_path = fields.CharField(max_length=512, null=True)
    error_log_path = fields.CharField(max_length=512, null=True)
    result_data = fields.JSONField(null=True)

    cpu_usage = fields.FloatField(null=True)
    memory_usage = fields.BigIntField(null=True)

    # 心跳时间 - 用于检测任务是否中断
    last_heartbeat = fields.DatetimeField(null=True, description="最后心跳时间")
    # 执行节点ID - 记录任务在哪个节点执行
    node_id = fields.BigIntField(null=True, db_index=True, description="执行节点ID")

    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "task_executions"
        indexes = [
            ("execution_id",),
            ("task_id",),
            ("status",),
            ("start_time",),
            ("created_at",),
            ("task_id", "status"),
            ("task_id", "start_time"),
            ("status", "start_time"),
            ("public_id",),
            ("node_id",),
            ("status", "last_heartbeat"),
        ]
        ordering = ["-start_time"]
