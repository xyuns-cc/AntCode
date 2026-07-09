"""Scheduler control event publisher."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from antcode_core.common.config import settings
from antcode_core.infrastructure.redis.streams import StreamClient


async def publish_scheduler_event(event: str, **payload: Any) -> str:
    """Publish a control-plane event for the Master process."""
    data = {
        "event": event,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    data.update({key: value for key, value in payload.items() if value is not None})
    return await StreamClient().xadd(
        settings.scheduler_event_stream,
        data,
        maxlen=settings.SCHEDULER_EVENT_MAXLEN,
    )
