"""Durably attach manual-retry lineage before completing its outbox event."""

from __future__ import annotations

from typing import Any

from antcode_core.application.services.scheduler.manual_retry_service import MANUAL_RETRYABLE_STATUSES
from antcode_core.domain.models.task_run import TaskRun
from tortoise.transactions import in_transaction


async def attach_manual_retry_lineage(target_run_id: str, source_run_id: str, task_id: int) -> None:
    async with in_transaction("default") as connection:
        source = await _locked_run(connection, source_run_id)
        target = await _locked_run(connection, target_run_id)
        _validate_runs(
            source,
            target,
            source_run_id=source_run_id,
            target_run_id=target_run_id,
            task_id=task_id,
        )
        result_data = dict(target.result_data or {})
        existing = result_data.get("retry_source_run_id")
        if existing is not None and existing != source_run_id:
            raise RuntimeError(f"manual retry lineage 冲突: target_run_id={target_run_id}")
        if existing == source_run_id:
            return
        result_data["retry_source_run_id"] = source_run_id
        updated = await TaskRun.filter(id=target.id).using_db(connection).update(result_data=result_data)
        if updated != 1:
            raise RuntimeError(f"manual retry lineage 写入失败: target_run_id={target_run_id}")


async def _locked_run(connection: Any, run_id: str) -> Any:
    return await TaskRun.filter(run_id=run_id).using_db(connection).select_for_update().first()


def _validate_runs(
    source: Any,
    target: Any,
    *,
    source_run_id: str,
    target_run_id: str,
    task_id: int,
) -> None:
    if source is None or source.task_id != task_id or source.status not in MANUAL_RETRYABLE_STATUSES:
        raise RuntimeError(f"manual retry source 已失效: source_run_id={source_run_id}")
    if target is None or target.task_id != task_id or target.run_id == source_run_id:
        raise RuntimeError(f"manual retry target 无效: target_run_id={target_run_id}")


__all__ = ["attach_manual_retry_lineage"]
