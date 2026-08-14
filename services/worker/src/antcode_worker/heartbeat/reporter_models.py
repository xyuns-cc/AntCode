"""Value objects emitted by the Worker heartbeat reporter."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class OSInfo:
    os_type: str = ""
    os_version: str = ""
    python_version: str = ""
    machine_arch: str = ""


@dataclass
class SpiderStats:
    request_count: int = 0
    response_count: int = 0
    item_scraped_count: int = 0
    error_count: int = 0
    avg_latency_ms: float = 0.0
    requests_per_minute: float = 0.0
    status_codes: dict = field(default_factory=dict)
    domain_stats: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Metrics:
    cpu: float = 0.0
    memory: float = 0.0
    disk: float = 0.0
    running_tasks: int = 0
    max_concurrent_tasks: int = 5
    task_count: int = 0
    project_count: int = 0
    env_count: int = 0
    queued_tasks: int = 0
    cpu_cores: int = 0
    memory_total_bytes: int = 0
    memory_used_bytes: int = 0
    memory_available_bytes: int = 0
    disk_total_bytes: int = 0
    disk_used_bytes: int = 0
    disk_free_bytes: int = 0
    uptime_seconds: int = 0
    spider_stats: SpiderStats | None = None


@dataclass
class Heartbeat:
    worker_id: str
    status: str
    metrics: Metrics
    os_info: OSInfo
    timestamp: datetime
    name: str = ""
    host: str = ""
    port: int = 0
    region: str = ""
    capabilities: dict = field(default_factory=dict)
    version: str = ""


__all__ = ["Heartbeat", "Metrics", "OSInfo", "SpiderStats"]
