"""Retry statistics derived from immutable TaskRun relationships."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from antcode_core.domain.models.enums import TaskStatus


def build_retry_stats(task_id: int, executions: Iterable[Any]) -> dict[str, Any]:
    rows = list(executions)
    retry_rows = [row for row in rows if _retry_source_run_id(row)]
    retry_sources = {str(row.run_id): _retry_source_run_id(row) for row in retry_rows}
    root_runs = {_retry_root(str(row.run_id), retry_sources) for row in retry_rows}
    retry_success = sum(row.status == TaskStatus.SUCCESS for row in retry_rows)
    total_retries = len(retry_rows)
    chain_count = len(root_runs)
    success_rate = retry_success / total_retries * 100 if total_retries else 0
    average = total_retries / chain_count if chain_count else 0
    return {
        "task_id": task_id,
        "total_executions": len(rows),
        "retried_executions": total_retries,
        "total_retries": total_retries,
        "retry_success_count": retry_success,
        "retry_success_rate": round(success_rate, 2),
        "avg_retries_per_execution": round(average, 2),
    }


def _retry_source_run_id(execution: Any) -> str | None:
    result_data = execution.result_data
    if not isinstance(result_data, dict):
        return None
    source = result_data.get("retry_source_run_id")
    return str(source) if source else None


def _retry_root(run_id: str, sources: dict[str, str | None]) -> str:
    visited: set[str] = set()
    current = run_id
    while current in sources:
        if current in visited:
            raise RuntimeError(f"TaskRun retry 关系存在循环: run_id={current}")
        visited.add(current)
        source = sources[current]
        if not source:
            return current
        current = source
    return current


__all__ = ["build_retry_stats"]
