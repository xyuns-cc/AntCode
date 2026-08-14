"""Compatibility adapter for pre-Lua Gateway control settlement test doubles."""

from typing import Any

from antcode_gateway.services.control_stream_ownership import (
    ControlAckOutcome,
    ControlPendingEntry,
)


async def ack_via_redis_xack(
    redis: Any,
    entry: ControlPendingEntry,
) -> ControlAckOutcome:
    acked = await redis.xack(entry.stream_key, entry.group, entry.message_id)
    if int(acked or 0) > 0:
        return ControlAckOutcome.ACKED
    return ControlAckOutcome.GONE
