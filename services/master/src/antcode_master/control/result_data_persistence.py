"""Transactional persistence for scheduler-owned TaskRun result metadata."""

from __future__ import annotations

from typing import Any

from antcode_core.domain.models.task_run import TaskRun
from tortoise.transactions import in_transaction

from antcode_master.control.result_metadata import merge_result_data


async def merge_dispatch_result_data(run_id: str, update: dict[str, Any]) -> dict[str, Any]:
    """Merge scheduler metadata without overwriting fields committed by a Worker."""
    async with in_transaction("default") as connection:
        execution = (
            await TaskRun.filter(run_id=str(run_id))
            .using_db(connection)
            .select_for_update()
            .only("id", "result_data")
            .first()
        )
        if execution is None:
            raise RuntimeError(f"TaskRun 不存在: run_id={run_id}")

        current = dict(execution.result_data or {})
        scheduler_fields = {key: value for key, value in update.items() if key not in current}
        merged = merge_result_data(current, scheduler_fields)
        updated = await TaskRun.filter(id=execution.id).using_db(connection).update(result_data=merged)
        if updated != 1:
            raise RuntimeError(f"TaskRun result_data 合并失败: run_id={run_id}")
        return merged


__all__ = ["merge_dispatch_result_data"]
