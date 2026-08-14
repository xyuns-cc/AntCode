"""Resolve authoritative Worker capabilities and normalize project task types."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from antcode_contracts.capabilities import validate_capabilities
from loguru import logger

from antcode_core.application.services.lease_capability_snapshot import (
    LeaseCapabilitySnapshot,
    read_live_capability_snapshots,
)
from antcode_core.application.services.lease_serialization import serialize_lease_value
from antcode_core.infrastructure.redis import get_redis_client

DEFAULT_EXECUTION_TASK_TYPE = "code"
PROJECT_TASK_TYPE_ALIASES = {"file": DEFAULT_EXECUTION_TASK_TYPE}


class WorkerCapabilityChangedError(RuntimeError):
    """Selected Worker no longer has a live matching capability snapshot."""


def required_execution_task_types(tasks: Sequence[dict[str, Any]]) -> frozenset[str]:
    return frozenset(_execution_task_type(task.get("project_type")) for task in tasks)


def _execution_task_type(project_type: object) -> str:
    value = str(project_type or DEFAULT_EXECUTION_TASK_TYPE).strip().lower()
    return PROJECT_TASK_TYPE_ALIASES.get(value, value)


def supports_task_types(capabilities: object, required: str | frozenset[str]) -> bool:
    if not isinstance(capabilities, dict):
        return False
    declared = capabilities.get("task_types")
    if not isinstance(declared, list):
        return False
    required_types = frozenset({required}) if isinstance(required, str) else required
    return required_types.issubset(declared)


def has_render_capability(capabilities: object) -> bool:
    if not isinstance(capabilities, dict):
        return False
    playwright = capabilities.get("playwright")
    return bool(isinstance(playwright, dict) and playwright.get("enabled"))


async def resolve_capability_map(workers: Sequence[Any], *, authoritative: bool) -> dict[int, dict[str, Any]]:
    if not authoritative:
        return {worker.id: worker.capabilities or {} for worker in workers}
    gateway_workers = [worker for worker in workers if str(worker.transport_mode or "").lower() == "gateway"]
    snapshots = await _gateway_snapshots(gateway_workers)
    return {worker.id: _resolved_capabilities(worker, snapshots) for worker in workers}


async def resolve_selection_capabilities(
    workers: Sequence[Any],
    require_render: bool,
    required: str | frozenset[str] | None,
) -> dict[int, dict[str, Any]]:
    return await resolve_capability_map(workers, authoritative=bool(require_render or required))


async def _gateway_snapshots(workers: Sequence[Any]) -> dict[str, str]:
    if not workers:
        return {}
    redis = await get_redis_client()
    snapshots = await read_live_capability_snapshots(redis, [worker.public_id for worker in workers])
    return {worker_id: snapshot.capabilities_json for worker_id, snapshot in snapshots.items() if snapshot.lease_id}


def _resolved_capabilities(worker: Any, snapshots: dict[str, str]) -> dict[str, Any]:
    raw: object = worker.capabilities or {}
    if str(worker.transport_mode or "").lower() == "gateway":
        raw = snapshots.get(worker.public_id, "")
    try:
        capabilities = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) and raw else raw
        return _validate_routing_capabilities(capabilities)
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError, ValueError):
        logger.warning("Worker capability 快照非法，排除候选: worker_id={}", worker.public_id)
        return {}


def _validate_routing_capabilities(capabilities: object) -> dict[str, Any]:
    return validate_capabilities(capabilities)


async def require_worker_current_requirements(
    worker: Any,
    *,
    require_render: bool,
    required: str | frozenset[str] | None,
) -> LeaseCapabilitySnapshot:
    redis = await get_redis_client()
    snapshots = await read_live_capability_snapshots(redis, [worker.public_id])
    snapshot = snapshots[worker.public_id]
    if not snapshot.lease_id or not snapshot.capabilities_json:
        raise WorkerCapabilityChangedError("Worker Lease 或能力快照已失效")
    capabilities = _resolved_capabilities(worker, {worker.public_id: snapshot.capabilities_json})
    if str(worker.transport_mode or "").lower() != "gateway":
        expected = serialize_lease_value(capabilities, preserve_empty=True)
        if expected != snapshot.capabilities_json:
            raise WorkerCapabilityChangedError("Direct Worker Lease 能力快照已变化")
    supported = (not require_render or has_render_capability(capabilities)) and (
        not required or supports_task_types(capabilities, required)
    )
    if not supported:
        raise WorkerCapabilityChangedError("Worker 能力或 Lease 在入队前已变化")
    return snapshot


def capability_requirement_label(required: str | frozenset[str]) -> str:
    values = sorted({required} if isinstance(required, str) else required)
    return repr(values[0]) if len(values) == 1 else repr(values)


__all__ = [
    "capability_requirement_label",
    "has_render_capability",
    "required_execution_task_types",
    "resolve_capability_map",
    "resolve_selection_capabilities",
    "supports_task_types",
    "require_worker_current_requirements",
    "WorkerCapabilityChangedError",
]
