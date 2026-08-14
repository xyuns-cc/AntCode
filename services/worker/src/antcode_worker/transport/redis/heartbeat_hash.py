"""Legacy Redis heartbeat hash projection for Direct workers."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from antcode_worker.transport.gateway.heartbeat_metrics import heartbeat_metrics_dict

OPTIONAL_HASH_FIELDS = frozenset(
    {
        "name",
        "host",
        "port",
        "region",
        "version",
        "os_type",
        "os_version",
        "python_version",
        "machine_arch",
        "capabilities",
        "spider_stats",
    }
)


async def write_legacy_heartbeat_hash(transport: Any, heartbeat: Any, worker_id: str) -> None:
    if transport._redis is None:
        return
    mapping = _heartbeat_mapping(heartbeat)
    heartbeat_key = transport._keys.heartbeat_key(worker_id)

    async def write() -> None:
        redis = transport._redis
        if redis is None:
            raise RuntimeError("Redis transport is not connected")
        pipeline = redis.pipeline(transaction=False)
        pipeline.hset(heartbeat_key, mapping=mapping)
        stale_fields = OPTIONAL_HASH_FIELDS.difference(mapping)
        if stale_fields:
            pipeline.hdel(heartbeat_key, *sorted(stale_fields))
        pipeline.expire(heartbeat_key, transport._config.heartbeat_interval * 3)
        await pipeline.execute()

    await transport._run_with_reconnect("写心跳 Hash", write)


def _heartbeat_mapping(heartbeat: Any) -> dict[str, str]:
    metrics = heartbeat_metrics_dict(heartbeat)
    timestamp = getattr(heartbeat, "timestamp", None) or datetime.now()
    mapping = {
        "status": str(getattr(heartbeat, "status", "online")),
        "cpu_percent": str(metrics["cpu"]),
        "memory_percent": str(metrics["memory"]),
        "disk_percent": str(metrics["disk"]),
        "running_tasks": str(metrics["running_tasks"]),
        "max_concurrent_tasks": str(metrics["max_concurrent_tasks"]),
        "task_count": str(metrics.get("task_count", 0)),
        "project_count": str(metrics.get("project_count", 0)),
        "env_count": str(metrics.get("env_count", 0)),
        "queued_tasks": str(metrics.get("queued_tasks", 0)),
        "cpu_cores": str(metrics.get("cpu_cores", 0)),
        "memory_total_bytes": str(metrics.get("memory_total_bytes", 0)),
        "memory_used_bytes": str(metrics.get("memory_used_bytes", 0)),
        "memory_available_bytes": str(metrics.get("memory_available_bytes", 0)),
        "disk_total_bytes": str(metrics.get("disk_total_bytes", 0)),
        "disk_used_bytes": str(metrics.get("disk_used_bytes", 0)),
        "disk_free_bytes": str(metrics.get("disk_free_bytes", 0)),
        "uptime_seconds": str(metrics.get("uptime_seconds", 0)),
        "timestamp": timestamp.isoformat(),
    }
    if metrics.get("spider_stats") is not None:
        mapping["spider_stats"] = json.dumps(metrics["spider_stats"], ensure_ascii=False, allow_nan=False)
    mapping.update(_optional_heartbeat_fields(heartbeat))
    return mapping


def _optional_heartbeat_fields(heartbeat: Any) -> dict[str, str]:
    os_info = getattr(heartbeat, "os_info", None)
    values = {
        "name": getattr(heartbeat, "name", None),
        "host": getattr(heartbeat, "host", None),
        "port": getattr(heartbeat, "port", None),
        "region": getattr(heartbeat, "region", None),
        "version": getattr(heartbeat, "version", None),
        "os_type": getattr(os_info, "os_type", None),
        "os_version": getattr(os_info, "os_version", None),
        "python_version": getattr(os_info, "python_version", None),
        "machine_arch": getattr(os_info, "machine_arch", None),
    }
    optional = {key: str(value) for key, value in values.items() if value}
    capabilities = getattr(heartbeat, "capabilities", None)
    if capabilities:
        optional["capabilities"] = json.dumps(capabilities, ensure_ascii=False, allow_nan=False)
    return optional
