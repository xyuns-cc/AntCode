"""Lease-generation-aware Redis task PEL claiming."""

from collections.abc import Awaitable, Callable
from typing import Any

from antcode_worker.transport.redis.reclaim_models import (
    PendingEntry,
    ReclaimConfig,
    parse_pending_entry,
    text,
)

PENDING_RANGE_END = "+"
PENDING_RANGE_START = "-"

_CLAIM_OLD_GENERATION_LUA = """
local group = ARGV[1]
local current_consumer = ARGV[2]
local min_idle_time = tonumber(ARGV[3])
if not min_idle_time or min_idle_time < 0 or min_idle_time ~= math.floor(min_idle_time) then
    return redis.error_reply('invalid reclaim min idle time')
end
if #ARGV < 5 or ((#ARGV - 3) % 2) ~= 0 then
    return redis.error_reply('invalid reclaim entry arguments')
end
local claimed = {}
for index = 4, #ARGV, 2 do
    local message_id = ARGV[index]
    local expected_consumer = ARGV[index + 1]
    if expected_consumer ~= current_consumer then
        local pending = redis.call('XPENDING', KEYS[1], group, message_id, message_id, 1)
        if #pending == 1 and pending[1][2] == expected_consumer then
            local messages = redis.call(
                'XCLAIM', KEYS[1], group, current_consumer, min_idle_time, message_id
            )
            if #messages == 1 then
                table.insert(claimed, messages[1])
            end
        end
    end
end
return claimed
"""


class GenerationPendingClaimer:
    def __init__(
        self,
        redis_client: Any,
        *,
        consumer_group: str,
        config: ReclaimConfig,
        require_current_generation: Callable[[], Awaitable[None]],
    ) -> None:
        self._redis = redis_client
        self._consumer_group = consumer_group
        self._config = config
        self._require_current_generation = require_current_generation

    async def find_and_claim(
        self,
        stream_key: str,
        current_consumer: str,
        *,
        max_count: int | None = None,
    ) -> list[tuple[str, dict[str, str]]]:
        count = self._config.max_reclaim_count if max_count is None else max_count
        if count < 1:
            return []
        entries = await self._find_old_generation_entries(stream_key, current_consumer, count)
        if not entries:
            return []
        await self._require_current_generation()
        args = self._claim_arguments(current_consumer, entries)
        result = await self._redis.eval(_CLAIM_OLD_GENERATION_LUA, 1, stream_key, *args)
        return _decode_claimed_messages(result)

    async def get_entry(self, stream_key: str, message_id: str) -> PendingEntry:
        try:
            result = await self._redis.xpending_range(
                stream_key,
                self._consumer_group,
                min=message_id,
                max=message_id,
                count=1,
            )
            if result:
                return parse_pending_entry(result[0])
        except Exception as exc:
            raise RuntimeError(f"读取 pending 信息失败: stream={stream_key} message={message_id}") from exc
        raise RuntimeError(f"XCLAIM 后消息不在 PEL: stream={stream_key} message={message_id}")

    async def _find_old_generation_entries(
        self,
        stream_key: str,
        current_consumer: str,
        max_count: int,
    ) -> list[PendingEntry]:
        selected: list[PendingEntry] = []
        start = PENDING_RANGE_START
        last_message_id = ""
        while len(selected) < max_count:
            await self._require_current_generation()
            result = await self._pending_page(stream_key, start, max_count)
            if not result:
                break
            entries = [parse_pending_entry(item) for item in result]
            selected.extend(self._eligible_old_entries(entries, current_consumer))
            last_message_id = _advance_pending_cursor(entries, last_message_id)
            start = f"({last_message_id}"
        return selected[:max_count]

    async def _pending_page(self, stream_key: str, start: str, count: int) -> Any:
        return await self._redis.xpending_range(
            stream_key,
            self._consumer_group,
            min=start,
            max=PENDING_RANGE_END,
            count=count,
        )

    def _eligible_old_entries(
        self,
        entries: list[PendingEntry],
        current_consumer: str,
    ) -> list[PendingEntry]:
        return [
            entry
            for entry in entries
            if entry.consumer != current_consumer and entry.idle_time_ms >= self._config.min_idle_time_ms
        ]

    def _claim_arguments(self, current_consumer: str, entries: list[PendingEntry]) -> list[str]:
        args = [self._consumer_group, current_consumer, str(self._config.min_idle_time_ms)]
        for entry in entries:
            args.extend((entry.message_id, entry.consumer))
        return args


def _advance_pending_cursor(entries: list[PendingEntry], previous: str) -> str:
    current = entries[-1].message_id
    if current == previous:
        raise RuntimeError("XPENDING cursor 未推进")
    return current


def _decode_claimed_messages(result: Any) -> list[tuple[str, dict[str, str]]]:
    if not isinstance(result, (list, tuple)):
        raise RuntimeError("条件 XCLAIM 返回值无效")
    messages: list[tuple[str, dict[str, str]]] = []
    for raw in result:
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            raise RuntimeError("条件 XCLAIM message 结构无效")
        messages.append((text(raw[0]), _decode_stream_fields(raw[1])))
    return messages


def _decode_stream_fields(raw: Any) -> dict[str, str]:
    if isinstance(raw, dict):
        return {text(key): text(value) for key, value in raw.items()}
    if not isinstance(raw, (list, tuple)) or len(raw) % 2 != 0:
        raise RuntimeError("条件 XCLAIM fields 结构无效")
    return {text(raw[index]): text(raw[index + 1]) for index in range(0, len(raw), 2)}
