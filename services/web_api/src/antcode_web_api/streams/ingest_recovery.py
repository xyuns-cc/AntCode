"""Bounded PostgreSQL storage-ID recovery windows for SSE cursors."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from antcode_web_api.streams.ingest_event_id import LogStreamCursor
from antcode_web_api.streams.ingest_recovery_query import (
    capture_recovery_snapshot,
    count_recovery_entries,
    fetch_recovery_page,
    resolve_cursor_storage_id,
)

# P1-SSE-02: 恢复分页的单次物化行数上界。消费方（BoundedRecoveryReplay）
# 逐行记行数 + 字节预算并在超限时关闭生成器，本 reader 只需保证预算触发
# 前后的单次 SELECT 物化有界——HTTP 单行 content 上限 1,048,576 字符
# （多字节 UTF-8 下接近 4 MiB），200 行大页一次可物化接近 800 MiB。
RECOVERY_PAGE_SIZE = 25
RecoveryCheckpoint = Callable[[], Awaitable[None]]


class RecoveryWindowError(RuntimeError):
    """The fixed PG recovery window changed while it was being read."""


@dataclass(frozen=True)
class RecoveryWindow:
    run_id: str
    cursor: LogStreamCursor
    start_id: int
    snapshot_id: int
    entry_count: int


class IngestRecoveryReader:
    """Resolve a cursor once, then stream a fixed PG ID interval."""

    async def materialize_after(self, run_id: str, cursor: LogStreamCursor) -> RecoveryWindow:
        snapshot_id = await capture_recovery_snapshot(run_id)
        lower_id = await resolve_cursor_storage_id(run_id, cursor, snapshot_id=snapshot_id)
        entry_count = await count_recovery_entries(
            run_id,
            lower_id=lower_id,
            snapshot_id=snapshot_id,
        )
        return RecoveryWindow(run_id, cursor, lower_id, snapshot_id, entry_count)

    async def iter_window(
        self,
        window: RecoveryWindow,
        *,
        checkpoint: RecoveryCheckpoint,
    ) -> AsyncGenerator[dict[str, Any], None]:
        after_id = window.start_id
        emitted = 0
        while after_id < window.snapshot_id:
            rows = await fetch_recovery_page(
                window.run_id,
                after_id=after_id,
                snapshot_id=window.snapshot_id,
                limit=RECOVERY_PAGE_SIZE,
            )
            if not rows:
                break
            next_after_id = _validate_page(rows, after_id, window.snapshot_id)
            for row in rows:
                await checkpoint()
                emitted += 1
                yield _normalize_pg_row(row)
            after_id = next_after_id
        if emitted != window.entry_count:
            raise RecoveryWindowError(f"PG 恢复窗口在回放期间发生变化: expected={window.entry_count} actual={emitted}")


def _normalize_pg_row(row: dict[str, Any]) -> dict[str, Any]:
    timestamp = row.get("timestamp")
    if timestamp is not None and not isinstance(timestamp, (datetime, str)):
        raise RecoveryWindowError(f"PG 恢复日志 timestamp 类型无效: {type(timestamp).__name__}")
    return {
        "log_type": row.get("log_type") or "stdout",
        "content": row.get("content") or "",
        "timestamp": timestamp.isoformat() if isinstance(timestamp, datetime) else timestamp or "",
        "sequence": int(row.get("sequence") or 0),
        "source": "pg_recovery",
        "event_id": row.get("event_id"),
        "storage_id": _storage_id(row),
    }


def _storage_id(row: dict[str, Any]) -> int:
    value = row.get("id")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RecoveryWindowError(f"PG 恢复日志 storage_id 无效: {value!r}")
    return value


def _validate_page(rows: list[dict[str, Any]], after_id: int, snapshot_id: int) -> int:
    previous = after_id
    for row in rows:
        storage_id = _storage_id(row)
        if storage_id <= previous or storage_id > snapshot_id:
            raise RecoveryWindowError("PG 恢复分页返回了越界或非递增 storage_id")
        previous = storage_id
    return previous


ingest_recovery_reader = IngestRecoveryReader()

__all__ = ["IngestRecoveryReader", "RECOVERY_PAGE_SIZE", "RecoveryCheckpoint", "RecoveryWindow"]
__all__ += ["RecoveryWindowError", "ingest_recovery_reader"]
