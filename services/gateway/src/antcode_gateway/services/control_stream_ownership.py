"""Lease-generation ownership and settlement for Gateway control streams."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from antcode_core.application.services.lease_service import (
    LEASE_RECORD_RETENTION_MS,
    LeaseConflictError,
)
from antcode_core.infrastructure.redis import redis_namespace

CONTROL_RECOVERY_PAGE_SIZE = 256
_RECOVERY_INITIAL_CURSOR = "-"
_RECOVERY_COMPLETE_CURSOR = "0-0"
_CLAIM_PAGE_REQUIRED_FIELDS = 2
_CLAIMED_MESSAGE_FIELDS = 2
_STREAM_FIELD_PAIR_SIZE = 2
_STALE_LEASE_OUTCOME = "stale_lease"

_ACK_OWNED_CONTROL_LUA = """
local current_lease = redis.call('HGET', KEYS[2], 'lease_id')
local lease_ttl = redis.call('PTTL', KEYS[2])
if current_lease ~= ARGV[4] or lease_ttl <= tonumber(ARGV[5]) then
    return 'stale_lease'
end
local pending = redis.call('XPENDING', KEYS[1], ARGV[1], ARGV[2], ARGV[2], 1)
if #pending == 0 then
    return 'gone'
end
if pending[1][2] ~= ARGV[3] then
    return 'not_owner'
end
local acked = redis.call('XACK', KEYS[1], ARGV[1], ARGV[2])
if acked ~= 1 then
    return 'ack_failed'
end
return 'acked'
"""

_CLAIM_FOREIGN_CONTROL_LUA = """
local current_lease = redis.call('HGET', KEYS[2], 'lease_id')
local lease_ttl = redis.call('PTTL', KEYS[2])
if current_lease ~= ARGV[3] or lease_ttl <= tonumber(ARGV[4]) then
    return {'stale_lease'}
end
local page = redis.call('XPENDING', KEYS[1], ARGV[1], ARGV[5], '+', ARGV[6])
local ids = {}
for _, item in ipairs(page) do
    if item[2] ~= ARGV[2] then
        table.insert(ids, item[1])
    end
end
local claimed = {}
if #ids > 0 then
    claimed = redis.call('XCLAIM', KEYS[1], ARGV[1], ARGV[2], 0, unpack(ids))
end
local next_cursor = '0-0'
if #page == tonumber(ARGV[6]) then
    next_cursor = '(' .. page[#page][1]
end
return {next_cursor, claimed}
"""


class ControlAckOutcome(StrEnum):
    ACKED = "acked"
    GONE = "gone"


class ControlPendingOwnerError(ValueError):
    """The PEL entry belongs to another Worker lease generation."""


@dataclass(frozen=True)
class ControlPendingEntry:
    worker_id: str
    lease_id: str
    stream_key: str
    group: str
    message_id: str
    consumer_name: str

    def __post_init__(self) -> None:
        expected = control_consumer_name(self.worker_id, self.lease_id)
        if self.consumer_name != expected:
            raise ValueError("控制事件 consumer 与 Worker 租约代际不一致")

    @property
    def lease_key(self) -> str:
        return _lease_key(self.worker_id)


@dataclass(frozen=True)
class ClaimedControlEntry:
    stream_key: str
    message_id: str
    data: dict[Any, Any]


@dataclass(frozen=True)
class _ControlLeaseIdentity:
    worker_id: str
    lease_id: str

    @property
    def consumer_name(self) -> str:
        return control_consumer_name(self.worker_id, self.lease_id)

    @property
    def lease_key(self) -> str:
        return _lease_key(self.worker_id)


def control_consumer_name(worker_id: str, lease_id: str) -> str:
    """Build the consumer identity shared by WatchControl and AckControl."""
    if not worker_id:
        raise ValueError("worker_id 不能为空，无法构造控制流 consumer")
    if not lease_id:
        raise ValueError("lease_id 不能为空，无法构造控制流 consumer")
    return f"{worker_id}:{lease_id}"


async def ack_owned_control_entry(
    redis: Any,
    entry: ControlPendingEntry,
) -> ControlAckOutcome:
    """Atomically require the exact generation owner and ACK one PEL entry."""
    raw = await redis.eval(
        _ACK_OWNED_CONTROL_LUA,
        2,
        entry.stream_key,
        entry.lease_key,
        entry.group,
        entry.message_id,
        entry.consumer_name,
        entry.lease_id,
        str(LEASE_RECORD_RETENTION_MS),
    )
    outcome = _text(raw)
    if outcome == ControlAckOutcome.ACKED:
        return ControlAckOutcome.ACKED
    if outcome == ControlAckOutcome.GONE:
        return ControlAckOutcome.GONE
    if outcome == "not_owner":
        raise ControlPendingOwnerError("控制事件不属于当前 Worker 租约代际的待确认队列")
    if outcome == _STALE_LEASE_OUTCOME:
        raise LeaseConflictError(entry.worker_id, entry.lease_id, "控制事件 ACK 时 lease 已切代")
    if outcome == "ack_failed":
        raise RuntimeError("控制事件仍在 PEL，但 Redis XACK 未确认")
    raise RuntimeError(f"控制事件 ACK Lua 返回未知状态: {outcome}")


async def recover_stale_control_entries(
    redis: Any,
    *,
    channels: tuple[Any, ...],
    worker_id: str,
    lease_id: str,
) -> tuple[ClaimedControlEntry, ...]:
    """Move foreign-generation PEL entries to the current WatchControl consumer."""
    identity = _ControlLeaseIdentity(worker_id, lease_id)
    claimed: list[ClaimedControlEntry] = []
    for channel in channels:
        claimed.extend(await _recover_channel(redis, channel, identity))
    return tuple(claimed)


async def stream_recovered_control_entries(
    redis: Any,
    session: Any,
    decode: Callable[..., Any],
) -> AsyncIterator[Any]:
    """Claim and decode entries that a stale Watch acquired after handoff."""
    claimed = await recover_stale_control_entries(
        redis,
        channels=session.channels,
        worker_id=session.worker_id,
        lease_id=session.lease_id,
    )
    for entry in claimed:
        event = await decode(
            redis,
            session,
            stream_key=entry.stream_key,
            message_id=entry.message_id,
            data=entry.data,
        )
        if event is not None:
            yield event


async def _recover_channel(
    redis: Any,
    channel: Any,
    identity: _ControlLeaseIdentity,
) -> list[ClaimedControlEntry]:
    cursor = _RECOVERY_INITIAL_CURSOR
    claimed: list[ClaimedControlEntry] = []
    while True:
        result = await redis.eval(
            _CLAIM_FOREIGN_CONTROL_LUA,
            2,
            channel.stream_key,
            identity.lease_key,
            channel.group,
            identity.consumer_name,
            identity.lease_id,
            str(LEASE_RECORD_RETENTION_MS),
            cursor,
            str(CONTROL_RECOVERY_PAGE_SIZE),
        )
        if _is_stale_lease(result):
            raise LeaseConflictError(identity.worker_id, identity.lease_id, "控制流 PEL 接管时 lease 已切代")
        next_cursor, messages = _decode_claim_page(result)
        claimed.extend(_claimed_entries(channel.stream_key, messages))
        if next_cursor == _RECOVERY_COMPLETE_CURSOR:
            return claimed
        if next_cursor == cursor:
            raise RuntimeError("控制流 PEL 接管 cursor 未推进")
        cursor = next_cursor


def _decode_claim_page(result: Any) -> tuple[str, list[Any]]:
    if not isinstance(result, (list, tuple)) or len(result) < _CLAIM_PAGE_REQUIRED_FIELDS:
        raise RuntimeError("控制流 PEL 接管返回值无效")
    messages = result[1]
    if not isinstance(messages, (list, tuple)):
        raise RuntimeError("控制流 PEL 接管 messages 返回值无效")
    return _text(result[0]), list(messages)


def _claimed_entries(stream_key: str, messages: list[Any]) -> list[ClaimedControlEntry]:
    entries: list[ClaimedControlEntry] = []
    for message in messages:
        if not isinstance(message, (list, tuple)) or len(message) != _CLAIMED_MESSAGE_FIELDS:
            raise RuntimeError("控制流 PEL 接管消息格式无效")
        message_id, data = message
        entries.append(ClaimedControlEntry(stream_key, _text(message_id), _claimed_payload(data)))
    return entries


def _claimed_payload(data: Any) -> dict[Any, Any]:
    if isinstance(data, dict):
        return data
    if not isinstance(data, (list, tuple)) or len(data) % _STREAM_FIELD_PAIR_SIZE:
        raise RuntimeError("控制流 PEL 接管消息 payload 无效")
    keys = data[::_STREAM_FIELD_PAIR_SIZE]
    values = data[1::_STREAM_FIELD_PAIR_SIZE]
    return dict(zip(keys, values, strict=True))


def _is_stale_lease(result: Any) -> bool:
    return isinstance(result, (list, tuple)) and len(result) == 1 and _text(result[0]) == _STALE_LEASE_OUTCOME


def _lease_key(worker_id: str) -> str:
    return f"{{{redis_namespace()}}}:lease:data:{worker_id}"


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="strict")
    return str(value)


__all__ = [
    "ClaimedControlEntry",
    "ControlAckOutcome",
    "ControlPendingEntry",
    "ControlPendingOwnerError",
    "ack_owned_control_entry",
    "control_consumer_name",
    "recover_stale_control_entries",
    "stream_recovered_control_entries",
]
