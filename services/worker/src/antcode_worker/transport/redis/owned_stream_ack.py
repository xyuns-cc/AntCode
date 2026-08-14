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


def describe_ack_failure(code: int, source: Any) -> str:
    """把 ACK 返回码翻成可定位的说明。

    0 = 条目已不在 PEL（多为重复 ACK）；-1 = PEL 归属另一个 consumer。两者的
    处置完全不同，不带判据的报错只能靠猜。
    """
    reason = "条目不在 PEL(疑重复 ACK)" if code == 0 else "PEL 属于其他 consumer"
    return (
        f"Direct 控制 ACK 响应无效: code={code} ({reason}) "
        f"stream={source.channel.stream_key} group={source.channel.group} "
        f"msg_id={source.message_id} consumer={source.consumer_name}"
    )
