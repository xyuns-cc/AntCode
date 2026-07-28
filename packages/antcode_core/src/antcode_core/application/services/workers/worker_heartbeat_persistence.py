"""Worker heartbeat persistence with row-level concurrency control."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from tortoise.transactions import in_transaction

from antcode_core.domain.models import Worker, WorkerHeartbeat, WorkerStatus


@dataclass(frozen=True, slots=True)
class WorkerHeartbeatUpdate:
    heartbeat_at: datetime
    status: str
    metrics: dict[str, Any]
    system_info: dict[str, str]
    capabilities: dict[str, Any] | None


def build_worker_heartbeat_update(
    *,
    heartbeat_at: datetime,
    status: str,
    metrics: dict[str, Any] | None,
    spider_stats: dict[str, Any] | None,
    system_info: dict[str, str | None],
    capabilities: dict[str, Any] | None,
) -> WorkerHeartbeatUpdate:
    heartbeat_metrics = dict(metrics or {})
    if spider_stats:
        heartbeat_metrics["spider_stats"] = spider_stats
    return WorkerHeartbeatUpdate(
        heartbeat_at=heartbeat_at,
        status=status,
        metrics=heartbeat_metrics,
        system_info={name: value for name, value in system_info.items() if value},
        capabilities=capabilities,
    )


def build_redis_heartbeat_update(data: dict[str, Any], heartbeat_at: datetime) -> WorkerHeartbeatUpdate:
    system_fields = ("version", "os_type", "os_version", "python_version", "machine_arch")
    capabilities = _redis_capabilities(data.get("capabilities"))
    return WorkerHeartbeatUpdate(
        heartbeat_at=heartbeat_at,
        status=WorkerStatus.ONLINE.value,
        metrics=_redis_metrics(data),
        system_info={name: data[name] for name in system_fields if data.get(name)},
        capabilities=capabilities,
    )


def _redis_capabilities(raw: Any) -> dict[str, Any] | None:
    if not raw:
        return None
    capabilities = json.loads(raw)
    if not isinstance(capabilities, dict):
        raise ValueError("Redis 心跳 capabilities 必须是 JSON object")
    return capabilities


def _redis_metrics(data: dict[str, Any]) -> dict[str, Any]:
    mappings = {
        "cpu_percent": ("cpu", float),
        "memory_percent": ("memory", float),
        "disk_percent": ("disk", float),
        "running_tasks": ("runningTasks", int),
        "max_concurrent_tasks": ("maxConcurrentTasks", int),
    }
    return {
        target: converter(data[source])
        for source, (target, converter) in mappings.items()
        if data.get(source) not in (None, "")
    }


async def persist_worker_heartbeat(
    worker_id: int,
    update: WorkerHeartbeatUpdate,
    *,
    require_newer: bool = False,
    record_history: bool = True,
) -> Worker | None:
    """Merge heartbeat-owned fields while holding the current Worker row lock."""
    async with in_transaction("default") as connection:
        worker = await Worker.filter(id=worker_id).using_db(connection).select_for_update().first()
        if not worker or (require_newer and not _is_newer(worker.last_heartbeat, update.heartbeat_at)):
            return None
        update_fields = _apply_update(worker, update)
        await worker.save(using_db=connection, update_fields=update_fields)
        if record_history:
            await WorkerHeartbeat.create(
                worker_id=worker.id,
                status=update.status,
                metrics=update.metrics or None,
                using_db=connection,
            )
        return worker


def _is_newer(current: datetime | None, incoming: datetime) -> bool:
    if current is None:
        return True
    if current.tzinfo is not None:
        current = current.astimezone().replace(tzinfo=None)
    if incoming.tzinfo is not None:
        incoming = incoming.astimezone().replace(tzinfo=None)
    return incoming > current


def _apply_update(worker: Worker, update: WorkerHeartbeatUpdate) -> list[str]:
    update_fields = ["last_heartbeat"]
    worker.last_heartbeat = update.heartbeat_at
    if worker.status != WorkerStatus.MAINTENANCE.value:
        worker.status = update.status
        update_fields.append("status")
    if update.metrics:
        worker.metrics = {**(worker.metrics or {}), **update.metrics}
        update_fields.append("metrics")
    for field_name, value in update.system_info.items():
        setattr(worker, field_name, value)
        update_fields.append(field_name)
    if update.capabilities is not None:
        worker.capabilities = update.capabilities
        update_fields.append("capabilities")
    return update_fields


__all__ = [
    "WorkerHeartbeatUpdate",
    "build_redis_heartbeat_update",
    "build_worker_heartbeat_update",
    "persist_worker_heartbeat",
]
