"""Redis persistence operations for Direct runtime-control evidence."""

from __future__ import annotations

from typing import Any

from antcode_core.infrastructure.redis import decode_stream_payload

from antcode_worker.transport.redis.runtime_control_evidence import (
    CONTROL_REPLY_MAXLEN,
    SettlementEvidenceError,
    encode_settlement_evidence,
    settlement_ttl_seconds,
)
from antcode_worker.transport.redis.runtime_control_models import ControlSource

_COMMIT_FENCED_MARKER_LUA = """
local current_lease = redis.call('HGET', KEYS[2], 'lease_id')
if current_lease == false or current_lease ~= ARGV[1] then
    return -2
end
local expires_at = tonumber(redis.call('HGET', KEYS[2], 'expires_at_ms'))
local now = redis.call('TIME')
local now_ms = tonumber(now[1]) * 1000 + math.floor(tonumber(now[2]) / 1000)
if expires_at == nil or expires_at <= now_ms or redis.call('PTTL', KEYS[2]) <= 0 then
    return -2
end
local existing = redis.call('GET', KEYS[1])
if existing then
    if existing ~= ARGV[2] then
        return -1
    end
    redis.call('PEXPIREAT', KEYS[1], ARGV[3])
    return 0
end
redis.call('SET', KEYS[1], ARGV[2], 'PXAT', ARGV[3], 'NX')
return 1
"""


async def require_pending_owner(redis: Any, source: ControlSource) -> None:
    pending = await redis.xpending_range(
        source.channel.stream_key,
        source.channel.group,
        min=source.message_id,
        max=source.message_id,
        count=1,
    )
    if len(pending) != 1:
        raise ValueError("Direct 控制事件不属于当前 consumer 的待确认队列")
    item = pending[0]
    if _text(item.get("message_id")) != source.message_id:
        raise ValueError("Direct 控制事件 PEL message_id 校验失败")
    if _text(item.get("consumer")) != source.consumer_name:
        raise ValueError("Direct 控制事件 PEL consumer 校验失败")


async def persist_reply_once(
    redis: Any,
    *,
    reply_stream: str,
    message_id: str,
    payload: dict[str, str],
    expires_at_ms: int,
) -> None:
    async with redis.pipeline(transaction=True) as pipeline:
        pipeline.xadd(
            reply_stream,
            payload,
            id=message_id,
            maxlen=CONTROL_REPLY_MAXLEN,
            approximate=False,
        )
        pipeline.pexpireat(reply_stream, expires_at_ms)
        response = await pipeline.execute()
    if not response[0] or not response[1]:
        raise RuntimeError("Direct 运行时控制结果持久化失败")


async def create_or_validate_marker(
    redis: Any,
    marker_key: str,
    payload: dict[str, str],
    *,
    lease_key: str,
    expected_lease_id: str,
    expires_at_ms: int,
) -> None:
    encoded = encode_settlement_evidence(payload)
    result = int(
        await redis.eval(
            _COMMIT_FENCED_MARKER_LUA,
            2,
            marker_key,
            lease_key,
            expected_lease_id,
            encoded,
            str(expires_at_ms),
        )
    )
    if result == -2:
        raise RuntimeError("Direct Worker lease generation 已失效")
    if result == -1:
        raise SettlementEvidenceError("Direct 运行时控制事件已使用不同结果完成")
    if result not in (0, 1):
        raise RuntimeError("Direct settlement marker Lua 响应无效")


async def settlement_expiry_ms(redis: Any, source: dict[str, Any]) -> int:
    redis_time = await redis.time()
    if not isinstance(redis_time, (list, tuple)) or len(redis_time) != 2:
        raise RuntimeError("Direct Redis TIME 响应无效")
    seconds, microseconds = (int(value) for value in redis_time)
    now_ms = seconds * 1000 + microseconds // 1000
    ttl_seconds = settlement_ttl_seconds(source, now_ms=now_ms)
    return now_ms + ttl_seconds * 1000


async def load_required_source_entry(
    redis: Any,
    stream_key: str,
    message_id: str,
) -> dict[str, Any]:
    decoded = await _load_optional_entry(redis, stream_key, message_id)
    if decoded is None:
        raise ValueError("Direct 原控制事件不存在")
    return decoded


async def load_optional_evidence_entry(
    redis: Any,
    stream_key: str,
    message_id: str,
) -> dict[str, Any] | None:
    try:
        return await _load_optional_entry(redis, stream_key, message_id)
    except ValueError as exc:
        raise SettlementEvidenceError("Direct 运行时控制 reply 证据损坏") from exc


async def _load_optional_entry(
    redis: Any,
    stream_key: str,
    message_id: str,
) -> dict[str, Any] | None:
    entries = await redis.xrange(stream_key, min=message_id, max=message_id, count=1)
    if not entries:
        return None
    stored_id, fields = entries[0]
    if _text(stored_id) != message_id:
        raise ValueError("Direct Redis Stream 事件 ID 不匹配")
    return decode_stream_payload(fields)


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="strict")
    return str(value)
