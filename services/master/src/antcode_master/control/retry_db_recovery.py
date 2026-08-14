"""Stable PostgreSQL recovery for durable retry intents."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from antcode_core.domain.models.task_run import TaskRun
from loguru import logger
from tortoise.expressions import Q

RECOVERY_BATCH_SIZE = 500


@dataclass(frozen=True)
class RecoveryCursor:
    retry_time: datetime
    row_id: int


async def recover_retry_intents(backend) -> int:
    recovered = 0
    cursor = None
    while True:
        pending = await load_recovery_page(cursor)
        if not pending:
            break
        for execution in pending:
            await _recover_execution(backend, execution)
            recovered += 1
        last = pending[-1]
        cursor = RecoveryCursor(last.next_retry_at, last.id)
        if len(pending) < RECOVERY_BATCH_SIZE:
            break
    if recovered:
        logger.info(f"从 TaskRun 恢复 retry 意图: {recovered} 条")
    return recovered


async def load_recovery_page(cursor: RecoveryCursor | None):
    query = TaskRun.filter(next_retry_at__not_isnull=True)
    if cursor is not None:
        query = query.filter(
            Q(next_retry_at__gt=cursor.retry_time) | Q(next_retry_at=cursor.retry_time, id__gt=cursor.row_id)
        )
    return await (
        query.order_by("next_retry_at", "id")
        .only("id", "task_id", "run_id", "retry_count", "next_retry_at")
        .limit(RECOVERY_BATCH_SIZE)
    )


async def _recover_execution(backend, execution) -> None:
    retry_time = execution.next_retry_at
    if retry_time is None:
        raise RuntimeError(f"recovery page 包含空 next_retry_at: run_id={execution.run_id}")
    await backend.schedule(
        task_id=execution.task_id,
        run_id=execution.run_id,
        retry_time=retry_time,
        retry_count=execution.retry_count,
    )


async def terminate_durable_intent(source_run_id: str) -> None:
    """Clear an intent whose target task can never execute."""
    if not source_run_id:
        raise RuntimeError("无法终止缺少 source run_id 的 retry intent")
    cleared = await TaskRun.filter(
        run_id=source_run_id,
        next_retry_at__not_isnull=True,
    ).update(next_retry_at=None)
    if cleared == 1:
        return
    source = await TaskRun.get_or_none(run_id=source_run_id)
    if source is not None and source.next_retry_at is not None:
        raise RuntimeError(f"终止 durable retry intent 失败: run_id={source_run_id}")


__all__ = [
    "RECOVERY_BATCH_SIZE",
    "RecoveryCursor",
    "load_recovery_page",
    "recover_retry_intents",
    "terminate_durable_intent",
]
