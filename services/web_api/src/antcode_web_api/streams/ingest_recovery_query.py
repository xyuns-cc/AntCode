"""Portable task-log storage-ID keyset queries for SSE recovery."""

from __future__ import annotations

from typing import Any

from antcode_web_api.streams.ingest_event_id import (
    IngestCursor,
    LogStreamCursor,
    PostgresLogCursor,
    format_postgres_cursor,
)


class RecoveryCursorUnavailableError(RuntimeError):
    """The requested cursor cannot be mapped to the authoritative PG log."""


class RecoveryCursorExpiredError(RecoveryCursorUnavailableError):
    """The requested cursor row has been removed from PostgreSQL."""


async def capture_recovery_snapshot(run_id: str) -> int:
    from antcode_core.domain.models.task_log import TaskLog

    rows = await TaskLog.filter(run_id=run_id).order_by("-id").limit(1).values("id")
    return int(rows[0].get("id") or 0) if rows else 0


async def resolve_cursor_storage_id(
    run_id: str,
    cursor: LogStreamCursor,
    *,
    snapshot_id: int,
) -> int:
    from antcode_core.domain.models.task_log import TaskLog

    filters: dict[str, Any] = {"run_id": run_id, "id__lte": snapshot_id}
    if isinstance(cursor, PostgresLogCursor):
        format_postgres_cursor(cursor.storage_id)
        filters["id"] = cursor.storage_id
    elif isinstance(cursor, IngestCursor):
        filters["event_id"] = cursor.event_id
    rows = await TaskLog.filter(**filters).limit(1).values("id")
    if not rows:
        raise RecoveryCursorExpiredError(f"日志 cursor 无法映射到 PG 快照: run_id={run_id} cursor={cursor.event_id}")
    return int(rows[0].get("id") or 0)


async def count_recovery_entries(run_id: str, *, lower_id: int, snapshot_id: int) -> int:
    from antcode_core.domain.models.task_log import TaskLog

    return await TaskLog.filter(run_id=run_id, id__gt=lower_id, id__lte=snapshot_id).count()


async def fetch_recovery_page(
    run_id: str,
    *,
    after_id: int,
    snapshot_id: int,
    limit: int,
) -> list[dict[str, Any]]:
    from antcode_core.domain.models.task_log import TaskLog

    query = TaskLog.filter(run_id=run_id, id__gt=after_id, id__lte=snapshot_id)
    rows = (
        await query.order_by("id")
        .limit(limit)
        .values(
            "id",
            "event_id",
            "run_id",
            "log_type",
            "content",
            "sequence",
            "timestamp",
            "level",
            "source",
        )
    )
    return list(rows or [])


__all__ = ["RecoveryCursorExpiredError", "RecoveryCursorUnavailableError", "capture_recovery_snapshot"]
__all__ += ["count_recovery_entries", "fetch_recovery_page", "resolve_cursor_storage_id"]
