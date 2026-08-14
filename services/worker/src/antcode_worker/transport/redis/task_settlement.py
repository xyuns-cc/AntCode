"""Atomic task ACK and requeue settlements for Direct Redis transport."""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from itertools import count
from typing import Any

from redis.cluster import key_slot

REQUEUE_SETTLEMENT_TTL_SECONDS = 7 * 24 * 60 * 60
REQUEUE_MARKER_CORRUPT = -4

_ACK_OWNED_TASK_LUA = """
local marker = redis.call('GET', KEYS[2])
if marker then
    if marker ~= ARGV[4] then
        return -2
    end
    redis.call('EXPIRE', KEYS[2], ARGV[5])
    return 0
end
local pending = redis.call('XPENDING', KEYS[1], ARGV[1], ARGV[2], ARGV[2], 1)
if #pending == 0 then
    return -1
end
if pending[1][2] ~= ARGV[3] then
    return -3
end
local acknowledged = redis.call('XACK', KEYS[1], ARGV[1], ARGV[2])
if acknowledged ~= 1 then
    return redis.error_reply('owned task XACK failed')
end
redis.call('SET', KEYS[2], ARGV[4], 'EX', ARGV[5])
return 1
"""

_REQUEUE_OWNED_TASK_LUA = """
local marker = redis.call('GET', KEYS[2])
if marker then
    if string.sub(marker, 1, 64) ~= ARGV[4] then
        return {-1, ''}
    end
    if string.sub(marker, 65, 65) ~= ':' or string.len(marker) <= 65 then
        return {-4, ''}
    end
    local existing_id = string.sub(marker, 66)
    if not string.match(existing_id, '^%d+%-%d+$') then
        return {-4, ''}
    end
    redis.call('EXPIREAT', KEYS[2], ARGV[5])
    return {0, existing_id}
end

local pending = redis.call('XPENDING', KEYS[1], ARGV[1], ARGV[2], ARGV[2], 1)
if #pending == 0 then
    return {-2, ''}
end
if pending[1][2] ~= ARGV[3] then
    return {-3, ''}
end
local new_id = redis.call('XADD', KEYS[1], '*', unpack(ARGV, 6))
local acknowledged = redis.call('XACK', KEYS[1], ARGV[1], ARGV[2])
if acknowledged ~= 1 then
    return redis.error_reply('owned task XACK failed')
end
redis.call('SET', KEYS[2], ARGV[4] .. ':' .. new_id, 'EXAT', ARGV[5])
return {1, new_id}
"""


async def ack_owned_task(
    redis: Any,
    *,
    stream_key: str,
    group: str,
    message_id: str,
    consumer_name: str,
) -> int:
    marker_key = _task_marker_key(stream_key, message_id, "ack")
    fingerprint = _task_identity_fingerprint(
        stream_key=stream_key,
        group=group,
        message_id=message_id,
        consumer_name=consumer_name,
    )
    result = await redis.eval(
        _ACK_OWNED_TASK_LUA,
        2,
        stream_key,
        marker_key,
        group,
        message_id,
        consumer_name,
        fingerprint,
        str(REQUEUE_SETTLEMENT_TTL_SECONDS),
    )
    return int(result or 0)


async def requeue_owned_task(
    redis: Any,
    *,
    stream_key: str,
    group: str,
    message_id: str,
    consumer_name: str,
    payload: dict[str, str],
) -> str:
    marker_key = requeue_marker_key(stream_key, message_id)
    fingerprint = _requeue_fingerprint(
        stream_key=stream_key,
        group=group,
        message_id=message_id,
        consumer_name=consumer_name,
        payload=payload,
    )
    expires_at = await _redis_expiry_seconds(redis)
    args: list[str] = [group, message_id, consumer_name, fingerprint, str(expires_at)]
    for key, value in payload.items():
        args.extend((key, value))
    result = await redis.eval(_REQUEUE_OWNED_TASK_LUA, 2, stream_key, marker_key, *args)
    status, new_id = int(result[0]), _text(result[1])
    if status == -1:
        raise RuntimeError("Direct task requeue settlement fingerprint 冲突")
    if status == -2:
        raise RuntimeError("Direct task requeue 原消息不在 PEL")
    if status == -3:
        raise RuntimeError("Direct task requeue PEL owner 已变更")
    if status == REQUEUE_MARKER_CORRUPT:
        raise RuntimeError("Direct task requeue settlement marker 损坏")
    if not new_id:
        raise RuntimeError("Direct task requeue 未返回新消息 ID")
    return new_id


def requeue_marker_key(stream_key: str, message_id: str) -> str:
    return _task_marker_key(stream_key, message_id, "requeue")


def _task_marker_key(stream_key: str, message_id: str, operation: str) -> str:
    digest = hashlib.sha256(message_id.encode("utf-8")).hexdigest()
    return f"{stream_key}:{operation}:{{{_co_slot_tag(stream_key)}}}:{digest}"


@lru_cache(maxsize=256)
def _co_slot_tag(stream_key: str) -> str:
    target_slot = key_slot(stream_key.encode("utf-8"))
    for candidate in count():
        tag = f"rq{candidate:x}"
        if key_slot(f"{{{tag}}}".encode("ascii")) == target_slot:
            return tag
    raise AssertionError("unreachable")


def _requeue_fingerprint(
    *,
    stream_key: str,
    group: str,
    message_id: str,
    consumer_name: str,
    payload: dict[str, str],
) -> str:
    canonical = json.dumps(
        {
            "consumer": consumer_name,
            "group": group,
            "message_id": message_id,
            "payload": payload,
            "stream": stream_key,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _task_identity_fingerprint(
    *,
    stream_key: str,
    group: str,
    message_id: str,
    consumer_name: str,
) -> str:
    value = f"{stream_key}|{group}|{message_id}|{consumer_name}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def _redis_expiry_seconds(redis: Any) -> int:
    redis_time = await redis.time()
    if not isinstance(redis_time, (list, tuple)) or len(redis_time) != 2:
        raise RuntimeError("Direct Redis TIME 响应无效")
    return int(redis_time[0]) + REQUEUE_SETTLEMENT_TTL_SECONDS


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="strict")
    return str(value)
