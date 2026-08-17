"""Deterministic identity shared by trigger producers and consumers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from antcode_core.domain.models.enums import ScheduleType

TRIGGER_RUN_NAMESPACE = uuid.UUID("5c1a5fd4-9a70-4a53-9d18-58f4d13ae0c2")
MANUAL_RETRY_OUTBOX_NAMESPACE = uuid.UUID("e3ba61f9-2797-4c68-835f-70cfa9bcd1b3")
SCHEDULED_RUN_NAMESPACE = uuid.UUID("db1e5b15-55e5-4d4e-94f4-a90c1a44ea9a")
ONE_TIME_SCHEDULE_TYPES = (ScheduleType.DATE, ScheduleType.ONCE)


def trigger_run_id(task_id: str | int, idempotency_key: str) -> str:
    if not idempotency_key:
        raise ValueError("trigger idempotency key must not be empty")
    return str(uuid.uuid5(TRIGGER_RUN_NAMESPACE, f"{task_id}:{idempotency_key}"))


def manual_retry_outbox_id(source_run_id: str) -> str:
    if not source_run_id:
        raise ValueError("manual retry source run ID must not be empty")
    return uuid.uuid5(MANUAL_RETRY_OUTBOX_NAMESPACE, source_run_id).hex


def is_one_time_schedule(task: Any) -> bool:
    return ScheduleType(task.schedule_type) in ONE_TIME_SCHEDULE_TYPES


def scheduled_fire_time(task: Any) -> datetime:
    fire_time = task.scheduled_time or task.created_at
    if fire_time is None:
        raise ValueError(f"一次性任务缺少持久时间锚点: task_id={task.id}")
    if fire_time.tzinfo is None:
        return fire_time.replace(tzinfo=UTC)
    return fire_time.astimezone(UTC)


def scheduled_run_id(task: Any) -> str:
    fire_time = scheduled_fire_time(task).isoformat(timespec="microseconds")
    return str(uuid.uuid5(SCHEDULED_RUN_NAMESPACE, f"{task.id}:{fire_time}"))


def dispatch_run_id(task: Any, idempotency_key: str) -> str:
    """派发一个任务“当下这一次执行”时使用的 run 身份。

    一次性任务（DATE/ONCE）整个生命周期只有一次逻辑执行，其身份由调度锚点
    唯一确定。耐久扫描与手动触发都必须落在这个 run id 上：两条路径各自取身份
    时去重域不相交，同一个一次性任务会被派发成两个 run 执行两次。周期任务的
    手动触发是一次额外执行，身份取自 outbox 幂等键。
    """
    if is_one_time_schedule(task):
        return scheduled_run_id(task)
    return trigger_run_id(task.id, idempotency_key)


__all__ = [
    "MANUAL_RETRY_OUTBOX_NAMESPACE",
    "ONE_TIME_SCHEDULE_TYPES",
    "SCHEDULED_RUN_NAMESPACE",
    "TRIGGER_RUN_NAMESPACE",
    "dispatch_run_id",
    "is_one_time_schedule",
    "manual_retry_outbox_id",
    "scheduled_fire_time",
    "scheduled_run_id",
    "trigger_run_id",
]
