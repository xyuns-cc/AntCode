"""
任务执行实例模型

任务执行记录的数据模型定义。
"""

from tortoise import fields

from antcode_core.domain.models.base import BaseModel, generate_public_id
from antcode_core.domain.models.enums import (
    DispatchStatus,
    RuntimeStatus,
    TaskStatus,
)


class TaskRun(BaseModel):
    """任务执行实例模型

    表示一次任务执行的完整记录，包含分发状态、运行状态和执行结果。
    """

    public_id = fields.CharField(
        max_length=32, unique=True, default=generate_public_id, db_index=True
    )
    task_id = fields.BigIntField()

    # 执行标识
    execution_id = fields.CharField(max_length=64, unique=True)

    # 时间信息
    start_time = fields.DatetimeField(null=True)
    end_time = fields.DatetimeField(null=True)
    duration_seconds = fields.FloatField(null=True)

    # 状态信息
    dispatch_status = fields.CharEnumField(DispatchStatus, default=DispatchStatus.PENDING)
    runtime_status = fields.CharEnumField(RuntimeStatus, null=True)
    status = fields.CharEnumField(TaskStatus)
    dispatch_updated_at = fields.DatetimeField(null=True)
    runtime_updated_at = fields.DatetimeField(null=True)

    # 执行结果
    exit_code = fields.IntField(null=True)
    error_message = fields.TextField(null=True)
    retry_count = fields.IntField(default=0)

    # 日志路径
    log_file_path = fields.CharField(max_length=512, null=True)
    error_log_path = fields.CharField(max_length=512, null=True)
    result_data = fields.JSONField(null=True)

    # 资源使用
    cpu_usage = fields.FloatField(null=True)
    memory_usage = fields.BigIntField(null=True)

    # 心跳时间 - 用于检测任务是否中断
    last_heartbeat = fields.DatetimeField(null=True, description="最后心跳时间")

    # 执行 Worker ID - 记录任务在哪个 Worker 执行
    worker_id = fields.BigIntField(
        null=True,
        db_index=True,
        description="执行 Worker ID",
    )

    # 时间戳
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "task_executions"
        indexes = [
            ("execution_id",),
            ("task_id",),
            ("status",),
            ("dispatch_status",),
            ("runtime_status",),
            ("start_time",),
            ("created_at",),
            ("task_id", "status"),
            ("task_id", "start_time"),
            ("status", "start_time"),
            ("public_id",),
            ("worker_id",),
            ("status", "last_heartbeat"),
        ]
        ordering = ["-start_time"]


__all__ = [
    "TaskRun",
]
