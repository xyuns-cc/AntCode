"""Read persisted log rows by their stable ingest event identities."""

from __future__ import annotations

from collections.abc import Sequence

from antcode_core.application.services.logs.postgres_log_service import (
    TASK_LOG_COLUMN_NAMES,
    PostgresLogEntry,
    row_to_log_entry,
)


async def list_persisted_log_events(event_ids: Sequence[str]) -> list[PostgresLogEntry]:
    """Return every requested row in request order or raise on contract drift."""
    requested = tuple(event_ids)
    if not requested:
        return []
    if any(not event_id for event_id in requested):
        raise ValueError("event_id 不能为空")
    if len(set(requested)) != len(requested):
        raise ValueError("event_id 批次内不得重复")

    from antcode_core.domain.models.task_log import TaskLog

    rows = await TaskLog.filter(event_id__in=requested).values(*TASK_LOG_COLUMN_NAMES)
    by_event_id = {_event_id(row): row_to_log_entry(row) for row in rows or []}
    missing = [event_id for event_id in requested if event_id not in by_event_id]
    if missing:
        raise RuntimeError(f"日志写入后无法按 event_id 回读: {missing}")
    return [by_event_id[event_id] for event_id in requested]


def _event_id(row: dict) -> str:
    """本模块的额外校验：回读行必须带非空 event_id。"""
    event_id = row.get("event_id")
    if not isinstance(event_id, str) or not event_id:
        raise RuntimeError("日志回读结果缺少 event_id")
    return event_id


__all__ = ["list_persisted_log_events"]
