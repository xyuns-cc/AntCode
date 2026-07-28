"""Atomic retry configuration updates and pending-intent reconciliation."""

from __future__ import annotations

from typing import Any

from tortoise.transactions import in_transaction

from antcode_core.application.services.scheduler.retry_cancellation_service import (
    build_retry_cancellation_changes,
)
from antcode_core.domain.models.task import Task
from antcode_core.domain.models.task_run import TaskRun


class RetryConfigurationConflictError(RuntimeError):
    pass


async def apply_retry_configuration(
    task_id: int,
    changes: dict[str, Any],
    *,
    user_id: int,
) -> list[str]:
    if not changes:
        return []
    async with in_transaction("default") as connection:
        task = await Task.filter(id=task_id).using_db(connection).select_for_update().first()
        if task is None:
            raise RetryConfigurationConflictError("任务已被并发删除")
        updated = await Task.filter(id=task.id).using_db(connection).update(**changes)
        if updated != 1:
            raise RetryConfigurationConflictError("任务重试配置更新冲突")
        maximum = changes.get("retry_count")
        if maximum is None:
            return []
        intents = (
            await TaskRun.filter(
                task_id=task.id,
                next_retry_at__not_isnull=True,
                retry_count__gt=int(maximum),
            )
            .using_db(connection)
            .select_for_update()
            .all()
        )
        return await _cancel_excess_intents(connection, intents, user_id=user_id)


async def _cancel_excess_intents(connection: Any, intents: list[Any], *, user_id: int) -> list[str]:
    cancelled: list[str] = []
    for execution in intents:
        changes = build_retry_cancellation_changes(execution, user_id=user_id)
        updated = await TaskRun.filter(id=execution.id).using_db(connection).update(**changes)
        if updated != 1:
            raise RetryConfigurationConflictError(f"待重试计划取消冲突: run_id={execution.run_id}")
        cancelled.append(str(execution.run_id))
    return cancelled


__all__ = ["RetryConfigurationConflictError", "apply_retry_configuration"]
