"""Decode authoritative Lease capabilities and maintain the legacy heartbeat view."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from antcode_contracts import control_pb2
from antcode_contracts.transcode import decode_capabilities
from loguru import logger

from antcode_gateway.handlers.heartbeat import LeaseData


@dataclass(frozen=True, slots=True)
class LeaseHeartbeatPayload:
    metrics: dict[str, Any] | None
    capabilities: dict[str, Any]
    heartbeat: LeaseData | None


def decode_lease_heartbeat(request: control_pb2.LeaseRequest, worker_id: str) -> LeaseHeartbeatPayload:
    capabilities = decode_capabilities(request.capabilities)
    if not request.HasField("metrics"):
        return LeaseHeartbeatPayload(None, capabilities, None)
    metrics = request.metrics
    values = {
        "cpu": metrics.cpu,
        "memory": metrics.memory,
        "disk": metrics.disk,
        "running_tasks": metrics.running_tasks,
        "max_concurrent_tasks": metrics.max_concurrent_tasks,
    }
    heartbeat = LeaseData(
        worker_id=worker_id,
        cpu=metrics.cpu,
        memory=metrics.memory,
        disk=metrics.disk,
        running_tasks=metrics.running_tasks,
        max_concurrent_tasks=metrics.max_concurrent_tasks,
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


__all__ = ["LeaseHeartbeatPayload", "decode_lease_heartbeat", "persist_legacy_heartbeat"]
