"""Shared Redis stream transport for cross-process SSE events."""

from __future__ import annotations

import json
from collections.abc import Awaitable
from typing import Any, cast

from loguru import logger

from antcode_core.infrastructure.redis.client import get_redis_client
from antcode_core.infrastructure.redis.control_plane import redis_namespace

SSE_EVENT_STREAM_MAXLEN = 20_000
SSE_EVENT_MAX_BYTES = 8 * 1024 * 1024
SSE_EVENT_STREAM_MAX_BYTES = 64 * 1024 * 1024
_REDIS_NOT_SUPPLIED = object()

_APPEND_EVENT_SCRIPT = """
local event_size = tonumber(ARGV[2])
local max_bytes = tonumber(ARGV[3])
local max_len = tonumber(ARGV[4])
if not event_size or event_size <= 0 or event_size % 1 ~= 0 then
    return redis.error_reply('SSE_EVENT_SIZE_INVALID')
end
if not max_bytes or max_bytes < event_size or max_bytes % 1 ~= 0 then
    return redis.error_reply('SSE_MAX_BYTES_INVALID')
end
if not max_len or max_len < 1 or max_len % 1 ~= 0 then
    return redis.error_reply('SSE_MAX_LEN_INVALID')
end
local total_raw = redis.call('GET', KEYS[2])
local stream_count = redis.call('XLEN', KEYS[1])
local order_count = redis.call('LLEN', KEYS[3])
local size_count = redis.call('HLEN', KEYS[4])
local reset = 0
local pristine = not total_raw and stream_count == 0 and order_count == 0 and size_count == 0
local total = tonumber(total_raw or '0')
local invalid = not pristine and (
    not total_raw or not total or total < 0 or total % 1 ~= 0 or
    (stream_count == 0 and total ~= 0) or (stream_count > 0 and total <= 0) or
    stream_count ~= order_count or stream_count ~= size_count
)
if invalid then
    redis.call('XTRIM', KEYS[1], 'MAXLEN', 0)
    redis.call('DEL', KEYS[3], KEYS[4])
    total = 0
    reset = 1
end
local event_id = redis.call('XADD', KEYS[1], '*', 'payload', ARGV[1])
redis.call('RPUSH', KEYS[3], event_id)
redis.call('HSET', KEYS[4], event_id, event_size)
total = total + event_size
local count = redis.call('LLEN', KEYS[3])
while total > max_bytes or count > max_len do
    local oldest_id = redis.call('LPOP', KEYS[3])
    if not oldest_id then break end
    local oldest_size = tonumber(redis.call('HGET', KEYS[4], oldest_id) or '')
    if not oldest_size or oldest_size <= 0 or oldest_size % 1 ~= 0 then
        redis.call('XTRIM', KEYS[1], 'MAXLEN', 0)
        redis.call('DEL', KEYS[3], KEYS[4])
        event_id = redis.call('XADD', KEYS[1], '*', 'payload', ARGV[1])
        redis.call('RPUSH', KEYS[3], event_id)
        redis.call('HSET', KEYS[4], event_id, event_size)
        total = event_size
        count = 1
        reset = 1
        break
    end
    redis.call('HDEL', KEYS[4], oldest_id)
    redis.call('XDEL', KEYS[1], oldest_id)
    total = math.max(0, total - oldest_size)
    count = count - 1
end
redis.call('SET', KEYS[2], total)
return {event_id, total, reset, count}
"""


class SSEEventPublishError(RuntimeError):
    """A realtime event was not durably appended to the Redis stream."""


def sse_event_stream_key(namespace: str | None = None) -> str:
    return f"{{{redis_namespace(namespace)}}}:log:sse-events"


def _accounting_keys(namespace: str | None = None) -> tuple[str, str, str, str]:
    stream = sse_event_stream_key(namespace)
    return stream, f"{stream}:bytes", f"{stream}:order", f"{stream}:sizes"


def encode_sse_event(message: dict[str, Any]) -> dict[Any, Any]:
    payload = json.dumps(
        message,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    return {"payload": payload}


async def publish_sse_event(
    message: dict[str, Any],
    *,
    redis: Any = _REDIS_NOT_SUPPLIED,
) -> None:
    """Append an event or raise so callers can retain their source message."""
    try:
        client = await get_redis_client() if redis is _REDIS_NOT_SUPPLIED else redis
        if client is None:
            raise RuntimeError("Redis 客户端不可用")
        payload = encode_sse_event(message)["payload"]
        payload_size = len(payload.encode("utf-8"))
        if payload_size > SSE_EVENT_MAX_BYTES:
            raise SSEEventPublishError(f"SSE 实时事件超过 {SSE_EVENT_MAX_BYTES} 字节上限")
        result = await cast(
            Awaitable[Any],
            client.eval(
                _APPEND_EVENT_SCRIPT,
                4,
                *_accounting_keys(),
                payload,
                payload_size,
                SSE_EVENT_STREAM_MAX_BYTES,
                SSE_EVENT_STREAM_MAXLEN,
            ),
        )
        if int(result[2]) == 1:
            logger.warning("SSE 实时 Stream 账本缺失或不一致，已重置瞬时流并由 PostgreSQL gap 恢复")
    except SSEEventPublishError:
        raise
    except Exception as exc:
        raise SSEEventPublishError("无法发布 SSE 实时事件") from exc


def decode_sse_event(fields: dict[Any, Any]) -> dict[str, Any]:
    raw = fields.get(b"payload") or fields.get("payload")
    if raw is None:
        raise ValueError("SSE 事件缺少 payload")
    text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
    value = json.loads(text, parse_constant=_reject_json_constant)
    if not isinstance(value, dict):
        raise ValueError("SSE 事件 payload 必须是对象")
    return value


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"SSE 事件 payload 包含非法 JSON 常量: {value}")


__all__ = [
    "SSE_EVENT_MAX_BYTES",
    "SSE_EVENT_STREAM_MAX_BYTES",
    "SSE_EVENT_STREAM_MAXLEN",
    "SSEEventPublishError",
    "decode_sse_event",
    "encode_sse_event",
    "publish_sse_event",
    "sse_event_stream_key",
]
