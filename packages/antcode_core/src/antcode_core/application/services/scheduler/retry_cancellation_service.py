"""Transactional cancellation of one durable retry intent."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from tortoise.transactions import in_transaction

from antcode_core.domain.models.enums import TaskStatus
from antcode_core.domain.models.task_run import TaskRun


class RetryIntentNotPendingError(RuntimeError):
    pass


async def cancel_retry_intent(run_id: str, *, user_id: int) -> None:
    async with in_transaction("default") as connection:
        execution = await TaskRun.filter(run_id=run_id).using_db(connection).select_for_update().first()
        if execution is None:
            raise RetryIntentNotPendingError("该执行没有待重试计划")
        if execution.next_retry_at is None:
            if _was_already_cancelled(execution):
                return
            raise RetryIntentNotPendingError("该执行没有待重试计划")
        changes = build_retry_cancellation_changes(execution, user_id=user_id)
        updated = await TaskRun.filter(id=execution.id).using_db(connection).update(**changes)
        if updated != 1:
            raise RuntimeError(f"取消 retry intent 失败: run_id={run_id}")


def build_retry_cancellation_changes(execution: Any, *, user_id: int) -> dict[str, Any]:
    cancelled_at = datetime.now(UTC)
    result_data = dict(execution.result_data or {})
    result_data.pop("retry_intent", None)
    result_data["retry_cancellation"] = {
        "cancelled_by_user_id": user_id,
        "cancelled_at": cancelled_at.isoformat(),
    }
    changes: dict[str, Any] = {
        "next_retry_at": None,
        "result_data": result_data,
    }
    if execution.status == TaskStatus.PENDING:
        changes.update(
            status=TaskStatus.CANCELLED,
            end_time=execution.end_time or cancelled_at,
            error_message=execution.error_message or f"重试已取消 by user {user_id}",
        )
    return changes


def _was_already_cancelled(execution: Any) -> bool:
    result_data = execution.result_data if isinstance(execution.result_data, dict) else {}
    return isinstance(result_data.get("retry_cancellation"), dict)


__all__ = [
    "RetryIntentNotPendingError",
    "build_retry_cancellation_changes",
    "cancel_retry_intent",
]
