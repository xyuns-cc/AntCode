"""Crawl progress and checkpoint value objects."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BatchProgress:
    """批次进度数据类。"""

    batch_id: str = ""
    project_id: str = ""
    total_urls: int = 0
    pending_urls: int = 0
    completed_urls: int = 0
    failed_urls: int = 0
    active_workers: int = 0
    speed_per_minute: float = 0.0
    last_updated: str = ""
    started_at: str = ""
    _completed_history: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "batch_id": self.batch_id,
            "project_id": self.project_id,
            "total_urls": self.total_urls,
            "pending_urls": self.pending_urls,
            "completed_urls": self.completed_urls,
            "failed_urls": self.failed_urls,
            "active_workers": self.active_workers,
            "speed_per_minute": self.speed_per_minute,
            "last_updated": self.last_updated,
            "started_at": self.started_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> BatchProgress:
        return cls(
            batch_id=data.get("batch_id", ""),
            project_id=data.get("project_id", ""),
            total_urls=int(data.get("total_urls", 0)),
            pending_urls=int(data.get("pending_urls", 0)),
            completed_urls=int(data.get("completed_urls", 0)),
            failed_urls=int(data.get("failed_urls", 0)),
            active_workers=int(data.get("active_workers", 0)),
            speed_per_minute=float(data.get("speed_per_minute", 0.0)),
            last_updated=data.get("last_updated", ""),
            started_at=data.get("started_at", ""),
        )


@dataclass
class Checkpoint:
    """检查点数据类。"""

    batch_id: str = ""
    project_id: str = ""
    progress: dict = field(default_factory=dict)
    queue_state: dict = field(default_factory=dict)
    created_at: str = ""

    def to_dict(self) -> dict:
        return {
            "batch_id": self.batch_id,
            "project_id": self.project_id,
            "progress": self.progress,
            "queue_state": self.queue_state,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Checkpoint:
        return cls(
            batch_id=data.get("batch_id", ""),
            project_id=data.get("project_id", ""),
            progress=data.get("progress", {}),
            queue_state=data.get("queue_state", {}),
            created_at=data.get("created_at", ""),
        )
