"""Atomic Redis Stream operations owned by the Crawl queue."""

from typing import Any

from antcode_core.common.serialization import to_json

_MOVE_PENDING_SCRIPT = """
if redis.call('EXISTS', KEYS[3]) == 1 then
    return redis.error_reply('CRAWL_PROJECT_DELETED')
end
local pending = redis.call("XPENDING", KEYS[1], ARGV[1], ARGV[2], ARGV[2], 1)
if #pending == 0 then
    return false
end

local xadd_args = {KEYS[2]}
local maxlen = tonumber(ARGV[3])
if maxlen and maxlen > 0 then
    table.insert(xadd_args, "MAXLEN")
    table.insert(xadd_args, "~")
    table.insert(xadd_args, maxlen)
end
table.insert(xadd_args, "*")
for index = 5, #ARGV do
    table.insert(xadd_args, ARGV[index])
end

local new_id = redis.call("XADD", unpack(xadd_args))
redis.call("XACK", KEYS[1], ARGV[1], ARGV[2])
redis.call("XDEL", KEYS[1], ARGV[2])
local ttl = tonumber(ARGV[4])
if ttl and ttl > 0 then redis.call("EXPIRE", KEYS[2], ttl) end
return new_id
"""

_XADD_ACTIVE_BATCH_SCRIPT = """
if redis.call('EXISTS', KEYS[2]) == 1 then
    return redis.error_reply('CRAWL_PROJECT_DELETED')
end
local ids = {}
local offset = 2
for message = 1, tonumber(ARGV[1]) do
    local field_count = tonumber(ARGV[offset])
    offset = offset + 1
    local args = {KEYS[1], '*'}
    for item = 1, field_count * 2 do
        table.insert(args, ARGV[offset])
        offset = offset + 1
    end
    table.insert(ids, redis.call('XADD', unpack(args)))
end
return ids
"""

_ACK_DELETE_SCRIPT = """
local acknowledged = 0
for index = 2, #ARGV do
    local pending = redis.call('XPENDING', KEYS[1], ARGV[1], ARGV[index], ARGV[index], 1)
    if #pending > 0 then
        acknowledged = acknowledged + redis.call('XACK', KEYS[1], ARGV[1], ARGV[index])
        redis.call('XDEL', KEYS[1], ARGV[index])
    end
end
return acknowledged
"""


async def xadd_batch_active(
    redis_client: Any,
    stream_key: str,
    messages: list[dict],
    *,
    deleted_fence_key: str,
) -> list[str]:
    if not messages:
        return []
    arguments: list[Any] = [len(messages)]
    for message in messages:
        fields = _serialize_fields(message)
        arguments.extend((len(fields) // 2, *fields))
    result = await redis_client.eval(
        _XADD_ACTIVE_BATCH_SCRIPT,
        2,
        stream_key,
        deleted_fence_key,
        *arguments,
    )
    return [_decode_text(message_id) for message_id in result]


async def xack_delete(
    redis_client: Any,
    stream_key: str,
    msg_ids: list[str],
    *,
    group_name: str,
) -> int:
    if not msg_ids:
        return 0
    return int(await redis_client.eval(_ACK_DELETE_SCRIPT, 1, stream_key, group_name, *msg_ids))


async def move_pending(
    redis_client: Any,
    source_key: str,
    destination_key: str,
    *,
    group_name: str,
    msg_id: str,
    data: dict,
    maxlen: int | None,
    expire_seconds: int | None,
    deleted_fence_key: str,
) -> str | None:
    if not data:
        raise ValueError("恢复消息数据不能为空")
    result = await redis_client.eval(
        _MOVE_PENDING_SCRIPT,
        3,
        source_key,
        destination_key,
        deleted_fence_key,
        group_name,
        msg_id,
        maxlen or 0,
        expire_seconds or 0,
        *_serialize_fields(data),
    )
    if not result:
        return None
    return _decode_text(result)


def _serialize_fields(data: dict) -> list[str | bytes]:
    serialized: list[str | bytes] = []
    for key, value in data.items():
        encoded = value if isinstance(value, (str, bytes)) else to_json(value)
        serialized.extend((key, encoded))
    return serialized


def _decode_text(value: Any) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)
