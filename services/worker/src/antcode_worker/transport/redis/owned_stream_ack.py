"""Atomically ACK a Redis Stream entry only for its current PEL owner."""

from __future__ import annotations

from typing import Any

_ACK_OWNED_STREAM_ENTRY_LUA = """
local pending = redis.call('XPENDING', KEYS[1], ARGV[1], ARGV[2], ARGV[2], 1)
if #pending == 0 then
    return 0
end
if pending[1][2] ~= ARGV[3] then
    return -1
end
return redis.call('XACK', KEYS[1], ARGV[1], ARGV[2])
"""


async def ack_owned_stream_entry(
    redis: Any,
    *,
    stream_key: str,
    group: str,
    message_id: str,
    consumer_name: str,
) -> int:
    result = await redis.eval(
        _ACK_OWNED_STREAM_ENTRY_LUA,
        1,
        stream_key,
        group,
        message_id,
        consumer_name,
    )
    return int(result or 0)
