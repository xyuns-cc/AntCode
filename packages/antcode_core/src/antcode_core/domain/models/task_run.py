"""
任务执行实例模型

任务执行记录的数据模型定义。
"""

from typing import Any

from tortoise import fields

from antcode_core.common.error_message_field import PersistedErrorMessageField
from antcode_core.domain.models.base import BaseModel, generate_public_id
from antcode_core.domain.models.enums import (
    DispatchStatus,
    RuntimeStatus,
    TaskStatus,
)

# 爬取批次派发的 run 不挂在任何 Task 行下，task_id 用该标记值表示"无关联 Task"。
# 这类 run 的项目与所有者统一由 result_data["crawl_batch_id"] 反查 CrawlBatch 得到，
# 见 workers/spider_run_access.py 与 workers/dispatch_authorization.py。
TASK_ID_ABSENT = 0


class TaskRun(BaseModel):
    """任务执行实例模型

    表示一次任务执行的完整记录，包含分发状态、运行状态和执行结果。
    """

    public_id = fields.CharField(max_length=32, unique=True, default=generate_public_id)
    task_id = fields.BigIntField()

    # 执行标识
    run_id = fields.CharField(max_length=64, unique=True)
    scheduler_fencing_token = fields.BigIntField(
        null=True,
        description="创建或接管该 run 的 Master 调度任期 token",
    )

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
    cancel_requested_at = fields.DatetimeField(
        null=True,
        description="已分配执行的取消请求时间；终态仍由 Worker 结果写入",
    )
    cancel_requested_by = fields.BigIntField(
        null=True,
        description="取消请求用户 ID；系统取消为空",
    )

    # 执行结果
    exit_code = fields.IntField(null=True)
    error_message = PersistedErrorMessageField(null=True)
    retry_count = fields.IntField(default=0)

    result_data: fields.JSONField[dict[str, Any] | None] = fields.JSONField(null=True)

    # 心跳时间 - 用于检测任务是否中断
    last_heartbeat = fields.DatetimeField(null=True, description="最后心跳时间")
    next_retry_at = fields.DatetimeField(null=True, description="下次重试时间")

    # 执行 Worker ID - 记录任务在哪个 Worker 执行
    worker_id = fields.BigIntField(
        null=True,
        db_index=True,
        description="执行 Worker ID",
    )
    lease_id = fields.CharField(
        max_length=64,
        null=True,
        db_index=True,
        description="Worker 执行该 run 时持有的 Lease 代际",
    )
    # P1-GW-04: Lease 代际的单调序号,fence bind 时的 Unix ms 时间戳。
    # bind_worker_run_lease_generation 用它做 CAS 谓词:
    #   WHERE lease_gen IS NULL OR lease_gen <= NEW.lease_gen
    # L1 迟到 bind 时 gen 较小 → CAS 拒绝,PG 保留 L2 的 lease_id。
    # null=True 兼容存量记录(旧 run 未标记 gen,首次 bind 时补齐)。
    lease_gen = fields.BigIntField(
        null=True,
        description="Lease 代际单调序号(fence 时点 Unix ms),防旧代际迟到 bind 覆盖",
    )

    # 时间戳
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "task_executions"
        indexes = [
            ("run_id",),
            ("task_id",),
            ("status",),
            ("dispatch_status",),
            ("runtime_status",),
            ("start_time",),
            ("created_at",),
            ("task_id", "status"),
            ("task_id", "start_time"),
            ("status", "start_time"),
            ("worker_id",),
            ("lease_id",),
            ("worker_id", "lease_id", "status"),
            ("status", "last_heartbeat"),
            ("status", "next_retry_at"),
        ]
        ordering = ["-start_time"]


__all__ = [
    "TASK_ID_ABSENT",
    "TaskRun",
]
