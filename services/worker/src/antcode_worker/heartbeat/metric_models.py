"""Immutable-shape data models produced by the Worker metrics collector."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CPUMetrics:
    percent: float = 0.0
    count: int = 0
    load_avg_1m: float = 0.0
    load_avg_5m: float = 0.0
    load_avg_15m: float = 0.0


@dataclass
class MemoryMetrics:
    percent: float = 0.0
    total_mb: float = 0.0
    available_mb: float = 0.0
    used_mb: float = 0.0


@dataclass
class DiskMetrics:
    percent: float = 0.0
    total_gb: float = 0.0
    free_gb: float = 0.0
    used_gb: float = 0.0


@dataclass
class NetworkMetrics:
    bytes_sent: int = 0
    bytes_recv: int = 0
    packets_sent: int = 0
    packets_recv: int = 0
    bytes_sent_rate: float = 0.0
    bytes_recv_rate: float = 0.0


@dataclass
class WorkerMetrics:
    running_slots: int = 0
    max_slots: int = 0
    queue_depth: int = 0
    total_tasks_executed: int = 0
    project_count: int = 0
    env_count: int = 0
    last_heartbeat_ts: float = 0.0
    reconnect_count: int = 0
    uptime_seconds: int = 0


@dataclass
class SystemMetrics:
    cpu: CPUMetrics = field(default_factory=CPUMetrics)
    memory: MemoryMetrics = field(default_factory=MemoryMetrics)
    disk: DiskMetrics = field(default_factory=DiskMetrics)
    network: NetworkMetrics = field(default_factory=NetworkMetrics)
    worker: WorkerMetrics = field(default_factory=WorkerMetrics)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cpu": {
                "percent": self.cpu.percent,
                "count": self.cpu.count,
                "load_avg_1m": self.cpu.load_avg_1m,
                "load_avg_5m": self.cpu.load_avg_5m,
                "load_avg_15m": self.cpu.load_avg_15m,
            },
            "memory": {
                "percent": self.memory.percent,
                "total_mb": self.memory.total_mb,
                "available_mb": self.memory.available_mb,
                "used_mb": self.memory.used_mb,
            },
            "disk": {
                "percent": self.disk.percent,
                "total_gb": self.disk.total_gb,
                "free_gb": self.disk.free_gb,
                "used_gb": self.disk.used_gb,
            },
            "network": {
                "bytes_sent": self.network.bytes_sent,
                "bytes_recv": self.network.bytes_recv,
                "packets_sent": self.network.packets_sent,
                "packets_recv": self.network.packets_recv,
                "bytes_sent_rate": self.network.bytes_sent_rate,
                "bytes_recv_rate": self.network.bytes_recv_rate,
            },
            "worker": {
                "running_slots": self.worker.running_slots,
                "max_slots": self.worker.max_slots,
                "queue_depth": self.worker.queue_depth,
                "total_tasks_executed": self.worker.total_tasks_executed,
                "project_count": self.worker.project_count,
                "env_count": self.worker.env_count,
                "last_heartbeat_ts": self.worker.last_heartbeat_ts,
                "reconnect_count": self.worker.reconnect_count,
            },
            "timestamp": self.timestamp,
        }


__all__ = [
    "CPUMetrics",
    "DiskMetrics",
    "MemoryMetrics",
    "NetworkMetrics",
    "SystemMetrics",
    "WorkerMetrics",
]
