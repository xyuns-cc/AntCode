"""Execution queue drain and sensitive-envelope inspection."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

# 只取线协议常量，不碰 `common.security` 聚合包：那条链会实例化控制面 Settings()，
# 而本脚本跑在只有 Redis 凭据的 `crawl-redis-upgrade` 容器里。
from antcode_core.common.task_payload_contract import ENVELOPE_FIELD, ENVELOPE_VERSION

from scripts.crawl_redis_upgrade_contract import (
    Blocker,
    ExecutionStoreStats,
    StreamGroupStats,
    StreamStats,
)

SCAN_COUNT = 500
_WORKER_SEGMENT = r"[A-Za-z0-9_-]+"


@dataclass(frozen=True)
class _EnvelopeCounts:
    version_one: int = 0
    unsupported: int = 0


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="strict")
    return str(value)


def _field(mapping: dict, name: str) -> Any:
    return mapping.get(name, mapping.get(name.encode()))


async def _scan_keys(client: Any, pattern: str) -> tuple[str, ...]:
    keys = {_text(key) async for key in client.scan_iter(match=pattern, count=SCAN_COUNT)}
    return tuple(sorted(keys))


async def _group_stats(client: Any, key: str, group: dict) -> StreamGroupStats:
    name = _text(_field(group, "name"))
    pending_info = await client.xpending(key, name)
    pending = int(_field(pending_info, "pending") or 0)
    lag = _field(group, "lag")
    return StreamGroupStats(name, pending, None if lag is None else int(lag))


async def inspect_stream(client: Any, key: str, *, inspect_envelopes: bool) -> tuple[StreamStats, list[Blocker]]:
    entries = int(await client.xlen(key))
    raw_groups = await client.xinfo_groups(key)
    groups = tuple([await _group_stats(client, key, group) for group in raw_groups])
    envelope = await _scan_stream_envelopes(client, key) if inspect_envelopes else _EnvelopeCounts()
    stats = StreamStats(key, entries, groups, envelope.version_one, envelope.unsupported)
    blockers = _stream_blockers(stats)
    blockers.extend(_envelope_blockers(key, envelope))
    return stats, blockers


def _stream_blockers(stats: StreamStats) -> list[Blocker]:
    if not stats.groups and stats.entries:
        return [Blocker("unconsumed_stream", stats.key, f"entries={stats.entries}; no consumer group")]
    blockers: list[Blocker] = []
    for group in stats.groups:
        if group.unconsumed is None:
            blockers.append(Blocker("unknown_stream_lag", stats.key, f"group={group.name}; Redis lag unavailable"))
        if group.pending or (group.unconsumed or 0):
            detail = f"group={group.name}, pel={group.pending}, unconsumed={group.unconsumed}"
            blockers.append(Blocker("execution_queue_not_drained", stats.key, detail))
    return blockers


async def _stream_pages(client: Any, key: str):
    minimum = "-"
    while True:
        page = await client.xrange(key, min=minimum, max="+", count=SCAN_COUNT)
        if not page:
            return
        for _message_id, fields in page:
            yield fields
        minimum = f"({_text(page[-1][0])}"


def _envelope_version(fields: dict) -> int | None:
    raw = _field(fields, ENVELOPE_FIELD)
    if raw is None:
        return None
    try:
        parsed = json.loads(_text(raw)) if not isinstance(raw, dict) else raw
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    version = parsed.get("version") if isinstance(parsed, dict) else None
    return version if isinstance(version, int) and not isinstance(version, bool) else None


async def _scan_stream_envelopes(client: Any, key: str) -> _EnvelopeCounts:
    version_one = 0
    unsupported = 0
    async for fields in _stream_pages(client, key):
        version = _envelope_version(fields)
        version_one += int(version == 1)
        unsupported += int(version != ENVELOPE_VERSION and version != 1)
    return _EnvelopeCounts(version_one, unsupported)


async def _scan_member_envelopes(values: Any) -> _EnvelopeCounts:
    version_one = 0
    unsupported = 0
    async for raw in values:
        try:
            payload = json.loads(_text(raw))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {}
        version = _envelope_version(payload) if isinstance(payload, dict) else None
        version_one += int(version == 1)
        unsupported += int(version != ENVELOPE_VERSION and version != 1)
    return _EnvelopeCounts(version_one, unsupported)


async def _redispatch_stats(client: Any, key: str, redis_type: str) -> ExecutionStoreStats:
    if redis_type == "zset":
        entries = int(await client.zcard(key))
        values = _zset_members(client.zscan_iter(key, count=SCAN_COUNT))
    else:
        entries = int(await client.hlen(key))
        values = _hash_fields(client.hscan_iter(key, count=SCAN_COUNT))
    envelope = await _scan_member_envelopes(values)
    return ExecutionStoreStats(key, redis_type, entries, envelope.version_one, envelope.unsupported)


async def _zset_members(values: Any):
    async for member, _score in values:
        yield member


async def _hash_fields(values: Any):
    async for field, _value in values:
        yield field


async def execution_inventory(client: Any, namespace: str):
    streams: list[StreamStats] = []
    stores: list[ExecutionStoreStats] = []
    blockers: list[Blocker] = []
    for key in await _ready_stream_keys(client, namespace):
        redis_type = _text(await client.type(key))
        if redis_type != "stream":
            blockers.append(Blocker("execution_store_type", key, f"expected=stream, actual={redis_type}"))
            continue
        stats, findings = await inspect_stream(client, key, inspect_envelopes=True)
        streams.append(stats)
        blockers.extend(findings)
    for key, expected_type in _redispatch_specs(namespace):
        redis_type = _text(await client.type(key))
        if redis_type == "none":
            continue
        if redis_type != expected_type:
            blockers.append(Blocker("execution_store_type", key, f"expected={expected_type}, actual={redis_type}"))
            continue
        store_stats = await _redispatch_stats(client, key, redis_type)
        stores.append(store_stats)
        blockers.extend(_execution_store_blockers(store_stats))
    return streams, stores, blockers


async def _ready_stream_keys(client: Any, namespace: str) -> tuple[str, ...]:
    """只匹配 ready 队列本体；``:requeue:`` / ``:ack:`` / ``:{dlq}:`` 等派生 key 由 worker 段的完整匹配剔除。"""
    candidates = await _scan_keys(client, f"{{{namespace}}}:task:ready:*")
    pattern = re.compile(rf"^\{{{re.escape(namespace)}\}}:task:ready:{_WORKER_SEGMENT}$")
    return tuple(sorted(key for key in candidates if pattern.fullmatch(key)))


def _redispatch_specs(namespace: str) -> tuple[tuple[str, str], ...]:
    return (
        (f"{{{namespace}}}:task:redispatch", "zset"),
        (f"{{{namespace}}}:task:redispatch:processing", "hash"),
    )


def _envelope_blockers(key: str, counts: _EnvelopeCounts) -> list[Blocker]:
    blockers: list[Blocker] = []
    if counts.version_one:
        blockers.append(Blocker("v1_envelope", key, f"entries={counts.version_one}"))
    if counts.unsupported:
        blockers.append(Blocker("unsupported_envelope", key, f"entries={counts.unsupported}"))
    return blockers


def _execution_store_blockers(stats: ExecutionStoreStats) -> list[Blocker]:
    blockers: list[Blocker] = []
    if stats.entries:
        blockers.append(Blocker("execution_queue_not_drained", stats.key, f"entries={stats.entries}"))
    counts = _EnvelopeCounts(stats.envelope_v1, stats.envelope_unsupported)
    blockers.extend(_envelope_blockers(stats.key, counts))
    return blockers


__all__ = ["execution_inventory", "inspect_stream"]
