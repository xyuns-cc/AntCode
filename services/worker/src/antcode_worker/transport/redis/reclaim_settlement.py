"""Atomic dead-letter settlement for reclaimed task messages."""

import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from antcode_worker.transport.redis.owned_stream_ack import ack_owned_stream_entry
from antcode_worker.transport.redis.reclaim_models import ReclaimConfig, ReclaimedTask, text

DEAD_LETTER_MAXLEN = 10_000
DEAD_LETTER_SLOT = "{dlq}"

_WRITE_DEAD_LETTER_LUA = """
local ttl = tonumber(ARGV[2])
local max_length = tonumber(ARGV[3])
if #ARGV < 5 or ((#ARGV - 3) % 2) ~= 0 then
    return redis.error_reply('invalid dead-letter field arguments')
end
if #ARGV[1] ~= 64 then
    return redis.error_reply('invalid dead-letter fingerprint')
end
if not ttl or ttl <= 0 or ttl ~= math.floor(ttl) then
    return redis.error_reply('invalid dead-letter ttl')
end
if not max_length or max_length <= 0 or max_length ~= math.floor(max_length) then
    return redis.error_reply('invalid dead-letter max length')
end
local existing = redis.call('GET', KEYS[2])
if existing then
    if string.sub(existing, 1, 64) ~= ARGV[1] then
        return {-1, ''}
    end
    local existing_id = string.sub(existing, 66)
    local entries = redis.call('XRANGE', KEYS[1], existing_id, existing_id, 'COUNT', 1)
    if #entries > 0 then
        redis.call('EXPIRE', KEYS[1], ttl)
        redis.call('EXPIRE', KEYS[2], ttl)
        return {0, existing_id}
    end
end
local entry_id = redis.call(
    'XADD', KEYS[1], 'MAXLEN', '~', max_length, '*', unpack(ARGV, 4)
)
redis.call('SET', KEYS[2], ARGV[1] .. ':' .. entry_id, 'EX', ttl)
redis.call('EXPIRE', KEYS[1], ttl)
return {1, entry_id}
"""


class DeadLetterSettlement:
    def __init__(
        self,
        redis_client: Any,
        *,
        consumer_group: str,
        config: ReclaimConfig,
        require_current_generation: Callable[[], Awaitable[None]],
        current_consumer_name: Callable[[], str],
    ) -> None:
        self._redis = redis_client
        self._consumer_group = consumer_group
        self._config = config
        self._require_current_generation = require_current_generation
        self._current_consumer_name = current_consumer_name

    async def settle(self, source_stream: str, task: ReclaimedTask) -> None:
        await self._require_current_generation()
        stream_key, marker_key = _dead_letter_keys(source_stream, task.message_id)
        fields = _dead_letter_fields(source_stream, task)
        await self.write_once(
            stream_key,
            marker_key,
            fields=fields,
            source_stream=source_stream,
            task=task,
        )
        await self._require_current_generation()
        acknowledged = await ack_owned_stream_entry(
            self._redis,
            stream_key=source_stream,
            group=self._consumer_group,
            message_id=task.message_id,
            consumer_name=self._current_consumer_name(),
        )
        await self.require_acknowledged(source_stream, task.message_id, acknowledged)

    async def write_once(
        self,
        stream_key: str,
        marker_key: str,
        *,
        fields: dict[str, Any],
        source_stream: str,
        task: ReclaimedTask,
    ) -> None:
        await self._require_current_generation()
        args = _dead_letter_arguments(
            self._config,
            source_stream,
            task,
            fields=fields,
        )
        result = await self._redis.eval(
            _WRITE_DEAD_LETTER_LUA,
            2,
            stream_key,
            marker_key,
            *args,
        )
        status = _settlement_status(result)
        if status < 0:
            raise RuntimeError("DLQ settlement fingerprint 冲突")

    async def require_acknowledged(
        self,
        stream_key: str,
        message_id: str,
        acknowledged: Any,
    ) -> None:
        if int(acknowledged or 0) == 1:
            return
        pending = await self._redis.xpending_range(
            stream_key,
            self._consumer_group,
            min=message_id,
            max=message_id,
            count=1,
        )
        if pending:
            raise RuntimeError(f"原消息 ACK 失败: {stream_key}:{message_id}")


def _dead_letter_fields(source_stream: str, task: ReclaimedTask) -> dict[str, str]:
    fields = dict(task.data)
    fields["_original_stream"] = source_stream
    fields["_original_message_id"] = task.message_id
    fields["_delivery_count"] = str(task.delivery_count)
    fields["_dead_lettered_at"] = datetime.now().isoformat()
    fields["_idle_time_ms"] = str(task.idle_time_ms)
    return fields


def _dead_letter_arguments(
    config: ReclaimConfig,
    source_stream: str,
    task: ReclaimedTask,
    *,
    fields: dict[str, Any],
) -> list[str]:
    fingerprint = _dead_letter_fingerprint(source_stream, task)
    args = [fingerprint, str(config.dead_letter_ttl_seconds), str(DEAD_LETTER_MAXLEN)]
    for key, value in fields.items():
        args.extend((text(key), text(value)))
    return args


def _dead_letter_keys(source_stream: str, message_id: str) -> tuple[str, str]:
    stream_key = f"{source_stream}:{DEAD_LETTER_SLOT}:dead_letter"
    identity = hashlib.sha256(f"{source_stream}|{message_id}".encode()).hexdigest()
    return stream_key, f"{stream_key}:settlement:{identity}"


def _dead_letter_fingerprint(source_stream: str, task: ReclaimedTask) -> str:
    normalized = {text(key): text(value) for key, value in task.data.items()}
    payload = json.dumps(
        {"message_id": task.message_id, "payload": normalized, "source_stream": source_stream},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _settlement_status(result: Any) -> int:
    if not isinstance(result, (list, tuple)) or len(result) != 2:
        raise RuntimeError("DLQ settlement 返回值无效")
    status = int(result[0])
    if status not in (-1, 0, 1):
        raise RuntimeError("DLQ settlement 状态无效")
    return status
