"""Decode authoritative Lease capabilities and maintain the legacy heartbeat view."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from antcode_contracts import control_pb2
from antcode_contracts.transcode import decode_capabilities
from antcode_core.application.services.lease_service import LeasePolicy
from loguru import logger

from antcode_gateway.handlers.heartbeat import LeaseData

HTTP_STATUS_MIN = 100
HTTP_STATUS_MAX = 599
MAX_DOMAIN_LENGTH = 253
MAX_PERCENTAGE = 100.0


@dataclass(frozen=True, slots=True)
class LeaseHeartbeatPayload:
    metrics: dict[str, Any] | None
    capabilities: dict[str, Any]
    heartbeat: LeaseData | None


def _nonnegative(value: int | float, field_name: str) -> int | float:
    if not math.isfinite(float(value)) or value < 0:
        raise ValueError(f"{field_name} must be finite and non-negative")
    return value


def _percentage(value: float, field_name: str) -> float:
    _nonnegative(value, field_name)
    if value > MAX_PERCENTAGE:
        raise ValueError(f"{field_name} must not exceed 100")
    return value


def _decode_domain_stats(item: Any) -> dict[str, Any]:
    domain = item.domain.strip()
    if not domain or len(domain) > MAX_DOMAIN_LENGTH:
        raise ValueError("spider domain must be between 1 and 253 characters")
    return {
        "domain": domain,
        "reqs": _nonnegative(item.request_count, "spider domain request_count"),
        "successRate": _percentage(item.success_rate, "spider domain success_rate"),
        "latency": _nonnegative(item.avg_latency_ms, "spider domain avg_latency_ms"),
    }


def _decode_status_codes(stats: Any) -> dict[str, int]:
    result = {}
    for code, count in stats.status_codes.items():
        if code < HTTP_STATUS_MIN or code > HTTP_STATUS_MAX:
            raise ValueError("spider status code must be between 100 and 599")
        result[str(code)] = int(_nonnegative(count, f"spider status_codes[{code}]"))
    return result


def _decode_spider_stats(metrics: Any) -> dict[str, Any] | None:
    if not metrics.HasField("spider_stats"):
        return None
    stats = metrics.spider_stats
    return {
        "request_count": _nonnegative(stats.request_count, "spider request_count"),
        "response_count": _nonnegative(stats.response_count, "spider response_count"),
        "item_scraped_count": _nonnegative(stats.item_scraped_count, "spider item_scraped_count"),
        "error_count": _nonnegative(stats.error_count, "spider error_count"),
        "avg_latency_ms": _nonnegative(stats.avg_latency_ms, "spider avg_latency_ms"),
        "requests_per_minute": _nonnegative(stats.requests_per_minute, "spider requests_per_minute"),
        "status_codes": _decode_status_codes(stats),
        "domain_stats": [_decode_domain_stats(item) for item in stats.domain_stats],
    }


def _decode_metrics_values(metrics: Any) -> dict[str, Any]:
    max_concurrent = int(_nonnegative(metrics.max_concurrent_tasks, "max_concurrent_tasks"))
    if max_concurrent < 1:
        raise ValueError("max_concurrent_tasks must be at least 1")
    return {
        "cpu": _percentage(metrics.cpu, "cpu"),
        "memory": _percentage(metrics.memory, "memory"),
        "disk": _percentage(metrics.disk, "disk"),
        "running_tasks": int(_nonnegative(metrics.running_tasks, "running_tasks")),
        "max_concurrent_tasks": max_concurrent,
        "task_count": int(_nonnegative(metrics.task_count, "task_count")),
        "project_count": int(_nonnegative(metrics.project_count, "project_count")),
        "env_count": int(_nonnegative(metrics.env_count, "env_count")),
        "queued_tasks": int(_nonnegative(metrics.queued_tasks, "queued_tasks")),
        "cpu_cores": int(_nonnegative(metrics.cpu_cores, "cpu_cores")),
        "memory_total_bytes": int(_nonnegative(metrics.memory_total_bytes, "memory_total_bytes")),
        "memory_used_bytes": int(_nonnegative(metrics.memory_used_bytes, "memory_used_bytes")),
        "memory_available_bytes": int(_nonnegative(metrics.memory_available_bytes, "memory_available_bytes")),
        "disk_total_bytes": int(_nonnegative(metrics.disk_total_bytes, "disk_total_bytes")),
        "disk_used_bytes": int(_nonnegative(metrics.disk_used_bytes, "disk_used_bytes")),
        "disk_free_bytes": int(_nonnegative(metrics.disk_free_bytes, "disk_free_bytes")),
        "uptime_seconds": int(_nonnegative(metrics.uptime_seconds, "uptime_seconds")),
    }


def decode_lease_heartbeat(request: control_pb2.LeaseRequest, worker_id: str) -> LeaseHeartbeatPayload:
    capabilities = decode_capabilities(request.capabilities)
    if not request.HasField("metrics"):
        return LeaseHeartbeatPayload(None, capabilities, None)
    metrics = request.metrics
    values = _decode_metrics_values(metrics)
    spider_stats = _decode_spider_stats(metrics)
    if spider_stats is not None:
        values["spider_stats"] = spider_stats
    heartbeat = LeaseData(
        worker_id=worker_id,
        cpu=values["cpu"],
        memory=values["memory"],
        disk=values["disk"],
        running_tasks=values["running_tasks"],
        max_concurrent_tasks=values["max_concurrent_tasks"],
        task_count=values["task_count"],
        project_count=values["project_count"],
        env_count=values["env_count"],
        queued_tasks=values["queued_tasks"],
        cpu_cores=values["cpu_cores"],
        memory_total_bytes=values["memory_total_bytes"],
        memory_used_bytes=values["memory_used_bytes"],
        memory_available_bytes=values["memory_available_bytes"],
        disk_total_bytes=values["disk_total_bytes"],
        disk_used_bytes=values["disk_used_bytes"],
        disk_free_bytes=values["disk_free_bytes"],
        uptime_seconds=values["uptime_seconds"],
        spider_stats=spider_stats,
        capabilities=capabilities,
    )
    return LeaseHeartbeatPayload(values, capabilities, heartbeat)


async def persist_legacy_heartbeat(handler: Any, heartbeat: LeaseData | None) -> None:
    if heartbeat is None:
        return
    try:
        persisted = await handler.handle(heartbeat)
    except Exception as exc:
        logger.warning("Lease 运维心跳视图写入异常: {}", type(exc).__name__)
        return
    if not persisted:
        logger.warning("Lease 运维心跳视图未写入")


def revoked_lease_response(policy: LeasePolicy, reason: str) -> control_pb2.LeaseResponse:
    """撤销响应同样带上权威 TTL/节拍，Worker 才能一致地校验不变量。"""
    return control_pb2.LeaseResponse(
        lease_id="",
        expires_at_ms=0,
        renew_after_ms=policy.renew_after_ms,
        ttl_ms=policy.ttl_ms,
        revoked=True,
        revoke_reason=reason,
    )


def granted_lease_response(lease: Any, policy: LeasePolicy) -> control_pb2.LeaseResponse:
    """签发响应必须下发权威 ``ttl_ms``。

    Worker 的 ``renew_after_ms * 2 < ttl_ms`` 不变量只有用服务端真正生效的
    TTL 才成立；缺了它 Worker 只能按本地默认值校验，服务端收紧 TTL 时守卫
    会静默通过，租约在两次续期之间过期 → master 补派 → 双执行。
    """
    return control_pb2.LeaseResponse(
        lease_id=lease.lease_id,
        expires_at_ms=lease.expires_at_ms,
        renew_after_ms=policy.renew_after_ms,
        ttl_ms=policy.ttl_ms,
        revoked=False,
    )


__all__ = [
    "LeaseHeartbeatPayload",
    "decode_lease_heartbeat",
    "granted_lease_response",
    "persist_legacy_heartbeat",
    "revoked_lease_response",
]
