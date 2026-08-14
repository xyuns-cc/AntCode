"""Idempotent result dead-letter insertion."""

from __future__ import annotations

from typing import Any

from antcode_contracts import data_pb2
from antcode_core.common.error_messages import normalize_persisted_error_message

DLQ_MAX_ENTRIES = 10_000

_IDEMPOTENT_XADD_LUA = """
local entries = redis.call('XRANGE', KEYS[1], '-', '+', 'COUNT', ARGV[3])
for _, entry in ipairs(entries) do
    local fields = entry[2]
    local source = nil
    local message = nil
    for index = 1, #fields, 2 do
        if fields[index] == 'orig_stream' then source = fields[index + 1] end
        if fields[index] == 'orig_msg_id' then message = fields[index + 1] end
    end
    if source == ARGV[1] and message == ARGV[2] then return entry[1] end
end
return redis.call('XADD', KEYS[1], 'MAXLEN', '=', ARGV[3], '*', unpack(ARGV, 4))
"""


async def insert_result_dead_letter(
    redis: Any,
    destination: str,
    *,
    source: str,
    message_id: str,
    entry: dict[str | bytes | int | float, bytes | str | int | float],
) -> None:
    fields = [item for pair in entry.items() for item in pair]
    await redis.eval(
        _IDEMPOTENT_XADD_LUA,
        1,
        destination,
        source,
        message_id,
        DLQ_MAX_ENTRIES,
        *fields,
    )


def sanitized_task_status_bytes(payload: Any) -> bytes:
    """复制结果帧时移除凭据并限制 error_message，避免 DLQ 成为旁路。"""
    message = data_pb2.TaskStatus()
    message.CopyFrom(payload)
    message.error_message = normalize_persisted_error_message(message.error_message) or ""
    return message.SerializeToString()


__all__ = ["insert_result_dead_letter", "sanitized_task_status_bytes"]
