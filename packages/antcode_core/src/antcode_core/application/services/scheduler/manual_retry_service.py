"""Transactionally replace an automatic retry intent with a manual trigger."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger
from tortoise.transactions import in_transaction

from antcode_core.application.services.scheduler.trigger_identity import manual_retry_outbox_id
from antcode_core.domain.models.enums import TaskStatus
from antcode_core.domain.models.task import Task
from antcode_core.domain.models.task_run import TaskRun

CancelPending = Callable[[str], Awaitable[int]]
EnqueueEvent = Callable[..., Awaitable[Any]]
GetEvent = Callable[[str, Any], Awaitable[Any | None]]

MANUAL_RETRYABLE_STATUSES = frozenset(
    {
        TaskStatus.SUCCESS,
        TaskStatus.FAILED,
        TaskStatus.TIMEOUT,
        TaskStatus.CANCELLED,
        TaskStatus.SKIPPED,
        TaskStatus.REJECTED,
    }
)


async def execute_manual_retry(
    run_id: str,
    user_id: int,
    *,
    cancel_pending: CancelPending,
    enqueue_event: EnqueueEvent,
    get_event: GetEvent,
) -> dict[str, Any]:
    """Consume an automatic intent and enqueue its manual replacement atomically."""
    async with in_transaction("default") as connection:
        execution = await _lock_source(connection, run_id)
        if execution is None:
            return _error("执行记录不存在")
        task = await Task.filter(id=execution.task_id).using_db(connection).first()
        if task is None:
            return _error("任务不存在")
        if execution.status not in MANUAL_RETRYABLE_STATUSES:
            return _error(f"仅终态执行可手动重试（当前状态: {execution.status}）")
        outbox_id = manual_retry_outbox_id(execution.run_id)
        if await get_event(outbox_id, connection):
            return _success(run_id, consumed=False, already_requested=True)
        consumed = execution.next_retry_at is not None
        if not consumed and await _automatic_retry_exists(connection, execution):
            return _error("自动重试已创建新的执行记录，请勿重复重试")
        redis_removed = await _replace_intent(
            connection,
            execution,
            cancel_pending=cancel_pending,
        )
        await enqueue_event(
            event_type="task_trigger",
            aggregate_type="manual_retry",
            aggregate_id=execution.run_id,
            payload={"task_id": str(task.id), "manual_retry_source_run_id": execution.run_id},
            connection=connection,
            public_id=outbox_id,
        )
    logger.info(
        "任务 {} 已手动触发重试 by user {} (source_run={}, auto_intent_consumed={}, redis_removed={})",
        task.name,
        user_id,
        run_id,
        consumed,
        redis_removed,
    )
    return _success(run_id, consumed=consumed, already_requested=False)


def _success(run_id: str, *, consumed: bool, already_requested: bool) -> dict[str, Any]:
    return {
        "success": True,
        "message": "已触发重试，将创建新的执行记录",
        "source_run_id": run_id,
        "auto_intent_consumed": consumed,
        "already_requested": already_requested,
    }


async def _lock_source(connection, run_id: str):
    return await TaskRun.filter(run_id=run_id).using_db(connection).select_for_update().first()


async def _automatic_retry_exists(connection, execution) -> bool:
    return (
        await TaskRun.filter(
            task_id=execution.task_id,
            result_data__contains={"retry_source_run_id": execution.run_id},
        )
        .using_db(connection)
        .exists()
    )


async def _replace_intent(connection, execution, *, cancel_pending: CancelPending) -> int:
    if execution.next_retry_at is None:
        return 0
    cleared = (
        await TaskRun.filter(id=execution.id, next_retry_at__not_isnull=True)
        .using_db(connection)
        .update(next_retry_at=None)
    )
    if cleared != 1:
        raise RuntimeError(f"手动重试消费自动 intent 失败: run_id={execution.run_id}")
    return await cancel_pending(execution.run_id)


def _error(message: str) -> dict[str, Any]:
    return {"success": False, "error": message}


__all__ = ["execute_manual_retry"]
