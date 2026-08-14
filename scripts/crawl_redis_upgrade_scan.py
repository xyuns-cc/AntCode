"""Read-only inventory and fail-closed checks for Crawl Redis upgrades."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from scripts.crawl_redis_upgrade_contract import (
    Blocker,
    LegacyKeyKind,
    ParsedLegacyKey,
    StateKeyStats,
    StreamStats,
    UpgradeMode,
    UpgradeReport,
    UpgradeRequest,
    parse_legacy_key,
)
from scripts.crawl_redis_upgrade_execution import execution_inventory, inspect_stream

SCAN_COUNT = 500
_STATE_TYPES = {
    LegacyKeyKind.PROGRESS: "hash",
    LegacyKeyKind.CHECKPOINT: "hash",
    LegacyKeyKind.DEDUP: "set",
}


@dataclass(frozen=True)
class _LegacyInspection:
    state: StateKeyStats | None
    stream: StreamStats | None
    blockers: tuple[Blocker, ...]


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="strict")
    return str(value)


async def _scan_keys(client: Any, pattern: str) -> tuple[str, ...]:
    keys = {_text(key) async for key in client.scan_iter(match=pattern, count=SCAN_COUNT)}
    return tuple(sorted(keys))


def _target_key(item: ParsedLegacyKey, namespace: str) -> str:
    project_id = item.project_id or ""
    if item.kind == LegacyKeyKind.PROGRESS:
        tag = _batch_tag(namespace, project_id, item.batch_id or "")
        return f"{tag}:progress"
    if item.kind == LegacyKeyKind.CHECKPOINT:
        tag = _batch_tag(namespace, project_id, item.batch_id or "")
        return f"{tag}:checkpoint"
    if item.kind == LegacyKeyKind.DEDUP:
        return f"{{{namespace}:crawl:{project_id}}}:dedup"
    raise ValueError(f"key 不可迁移: {item.key}")


def _batch_tag(namespace: str, project_id: str, batch_id: str) -> str:
    return f"{{{namespace}:crawl:{project_id}:{batch_id}}}"


def _paused_authorized(item: ParsedLegacyKey, request: UpgradeRequest) -> bool:
    if item.kind == LegacyKeyKind.DEDUP:
        return item.project_id in request.paused_projects
    return (item.project_id, item.batch_id) in request.paused_batches


async def _state_stats(client: Any, item: ParsedLegacyKey, request: UpgradeRequest) -> StateKeyStats:
    redis_type = _text(await client.type(item.key))
    if redis_type == "none":
        item_count = 0
    elif redis_type == "hash":
        item_count = int(await client.hlen(item.key))
    elif redis_type == "set":
        item_count = int(await client.scard(item.key))
    else:
        item_count = -1
    return StateKeyStats(
        source=item.key,
        target=_target_key(item, request.namespace),
        kind=item.kind.value,
        redis_type=redis_type,
        items=item_count,
        pttl_ms=int(await client.pttl(item.key)),
        authorized_paused_state=_paused_authorized(item, request),
    )


async def _state_blockers(client: Any, stats: StateKeyStats) -> list[Blocker]:
    blockers: list[Blocker] = []
    expected = "set" if stats.kind == LegacyKeyKind.DEDUP.value else "hash"
    if stats.redis_type != expected:
        detail = f"expected={expected}, actual={stats.redis_type}"
        if stats.kind == LegacyKeyKind.DEDUP.value:
            detail += "; RedisBloom module filters cannot be converted to an exact Set"
        blockers.append(Blocker("unsupported_state_type", stats.source, detail))
        return blockers
    if stats.pttl_ms < -1 or stats.pttl_ms == 0:
        blockers.append(Blocker("invalid_source_ttl", stats.source, f"pttl_ms={stats.pttl_ms}"))
    if not stats.authorized_paused_state:
        blockers.append(Blocker("state_not_declared_paused", stats.source, "missing explicit paused project/batch"))
    target_type = _text(await client.type(stats.target))
    if target_type not in {"none", expected}:
        blockers.append(Blocker("target_type_conflict", stats.target, f"expected={expected}, actual={target_type}"))
    if expected == "hash" and target_type == expected:
        source_value = await client.hgetall(stats.source)
        target_value = await client.hgetall(stats.target)
        if source_value != target_value:
            blockers.append(Blocker("target_content_conflict", stats.target, "target Hash differs from legacy source"))
    return blockers


async def _inspect_legacy_key(client: Any, item: ParsedLegacyKey, request: UpgradeRequest) -> _LegacyInspection:
    if item.kind in _STATE_TYPES:
        stats = await _state_stats(client, item, request)
        return _LegacyInspection(stats, None, tuple(await _state_blockers(client, stats)))
    if item.kind == LegacyKeyKind.STREAM:
        stream_stats, findings = await inspect_stream(client, item.key, inspect_envelopes=False)
        return _LegacyInspection(None, stream_stats, tuple(findings))
    detail = "legacy key requires explicit export or deletion after writers stop"
    return _LegacyInspection(None, None, (Blocker("unsupported_legacy_key", item.key, detail),))


async def _fresh_deploy_blockers(client: Any, request: UpgradeRequest, legacy_keys: set[str]) -> list[Blocker]:
    blockers = []
    if legacy_keys:
        blockers.append(Blocker("fresh_deploy_has_legacy_data", "rule:*/crawl:*", f"keys={len(legacy_keys)}"))
    tagged_pattern = f"{{{request.namespace}:crawl:*}}:*"
    runtime_pattern = f"{request.namespace}:crawl:*"
    current = set(await _scan_keys(client, tagged_pattern))
    current.update(await _scan_keys(client, runtime_pattern))
    if current:
        detail = f"keys={len(current)}; patterns={tagged_pattern},{runtime_pattern}"
        blockers.append(Blocker("fresh_deploy_has_current_data", request.namespace, detail))
    return blockers


async def build_report(client: Any, request: UpgradeRequest) -> UpgradeReport:
    request.validate()
    legacy_keys = set(await _scan_keys(client, "rule:*"))
    legacy_keys.update(await _scan_keys(client, "crawl:*"))
    state_stats: list[StateKeyStats] = []
    streams: list[StreamStats] = []
    blockers: list[Blocker] = []
    for key in sorted(legacy_keys):
        item = parse_legacy_key(key)
        inspected = await _inspect_legacy_key(client, item, request)
        if inspected.state is not None:
            state_stats.append(inspected.state)
        if inspected.stream is not None:
            streams.append(inspected.stream)
        blockers.extend(inspected.blockers)
    execution_streams, stores, findings = await execution_inventory(client, request.namespace)
    streams.extend(execution_streams)
    blockers.extend(findings)
    if request.mode == UpgradeMode.FRESH_DEPLOY:
        blockers.extend(await _fresh_deploy_blockers(client, request, legacy_keys))
    return UpgradeReport(
        request.mode.value,
        request.namespace,
        request.apply,
        tuple(sorted(legacy_keys)),
        tuple(state_stats),
        tuple(streams),
        tuple(stores),
        tuple(blockers),
    )


__all__ = ["build_report"]
