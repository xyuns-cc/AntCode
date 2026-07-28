"""Web API compatibility adapter for the shared SSE event stream."""

from __future__ import annotations

from typing import Any

import antcode_core.infrastructure.redis.sse_event_stream as shared_stream
from antcode_core.infrastructure.redis import get_redis_client

SSE_EVENT_STREAM_MAXLEN = shared_stream.SSE_EVENT_STREAM_MAXLEN
decode_sse_event = shared_stream.decode_sse_event
sse_event_stream_key = shared_stream.sse_event_stream_key


async def publish_sse_event(message: dict[str, Any]) -> None:
    redis = await get_redis_client()
    await shared_stream.publish_sse_event(message, redis=redis)


__all__ = ["decode_sse_event", "publish_sse_event", "sse_event_stream_key"]
