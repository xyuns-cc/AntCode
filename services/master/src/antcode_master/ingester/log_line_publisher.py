"""Publish database-authoritative log rows to realtime SSE consumers."""

from __future__ import annotations

from collections.abc import Sequence

from antcode_core.application.services.logs.postgres_log_service import PostgresLogEntry
from antcode_core.common.realtime_events import build_log_line_message
from antcode_core.infrastructure.redis.sse_event_stream import publish_sse_event


async def publish_persisted_log_lines(entries: Sequence[PostgresLogEntry]) -> None:
    for entry in entries:
        if not entry.event_id:
            raise RuntimeError("持久化日志缺少 event_id，禁止发布不稳定实时事件")
        await publish_sse_event(
            build_log_line_message(
                entry.run_id,
                log_type=entry.log_type,
                content=entry.content,
                timestamp=entry.timestamp.isoformat() if entry.timestamp else None,
                sequence=entry.sequence,
                source="realtime",
                event_id=entry.event_id,
                storage_id=entry.storage_id,
            )
        )


__all__ = ["publish_persisted_log_lines"]
