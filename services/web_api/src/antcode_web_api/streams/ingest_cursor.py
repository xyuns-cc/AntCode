"""Redis SSE follower cursor initialization."""

from __future__ import annotations

from typing import Any

from antcode_web_api.streams.ingest_decoder import decode_value


async def initial_stream_cursors(
    redis: Any,
    *,
    event_key: str,
    resume_cursors: dict[str, str],
) -> dict[str, str]:
    """Return the SSE event cursor, sampling its fixed tail on first start."""
    event_cursor = resume_cursors.get(event_key)
    if event_cursor is None:
        event_cursor = await latest_stream_id(redis, event_key)
    return {event_key: event_cursor}


async def latest_stream_id(redis: Any, stream_key: str) -> str:
    """Resolve the current tail to a fixed Redis stream ID."""
    messages = await redis.xrevrange(stream_key, count=1)
    if not messages:
        return "0-0"
    return decode_value(messages[0][0])


__all__ = ["initial_stream_cursors", "latest_stream_id"]
