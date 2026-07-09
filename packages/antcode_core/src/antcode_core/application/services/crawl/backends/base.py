"""爬虫 Redis 队列后端抽象基类。"""

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from antcode_core.common.config import settings


def _redis_backend_required(*env_names: str) -> None:
    for env_name in env_names:
        value = os.getenv(env_name, "").strip().lower()
        if value and value != "redis":
            raise ValueError("爬虫队列只能使用 Redis")
    if settings.CRAWL_BACKEND.strip().lower() != "redis":
        raise ValueError("爬虫队列只能使用 Redis")


@dataclass
class QueueTask:
    """队列任务数据"""

    msg_id: str = ""
    url: str = ""
    method: str = "GET"
    headers: dict = field(default_factory=dict)
    depth: int = 0
    priority: int = 5
    retry_count: int = 0
    parent_url: str | None = None
    batch_id: str = ""
    project_id: str = ""
    status: str = "pending"
    created_at: float = 0.0

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "url": self.url,
            "method": self.method,
            "headers": self.headers or {},
            "depth": self.depth,
            "priority": self.priority,
            "retry_count": self.retry_count,
            "parent_url": self.parent_url or "",
            "batch_id": self.batch_id,
            "project_id": self.project_id,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict, msg_id: str = "") -> "QueueTask":
        """从字典创建任务"""
        return cls(
            msg_id=msg_id,
            url=data.get("url", ""),
            method=data.get("method", "GET"),
            headers=data.get("headers") or {},
            depth=int(data.get("depth", 0)),
            priority=int(data.get("priority", 5)),
            retry_count=int(data.get("retry_count", 0)),
            parent_url=data.get("parent_url") or None,
            batch_id=data.get("batch_id", ""),
            project_id=data.get("project_id", ""),
            status=data.get("status", "pending"),
        )


@dataclass
class QueueStats:
    """队列统计信息"""

    pending: int = 0
    processing: int = 0
    total: int = 0
    dead_letter: int = 0


@dataclass
class QueueMetrics:
    """队列指标信息"""

    queue_length: int = 0
    pending_count: int = 0
    consumers: dict = field(default_factory=dict)


@dataclass
class ReclaimedTask:
    """回收的超时任务"""

    task: QueueTask
    delivery_count: int = 1


class CrawlQueueBackend(ABC):
    """爬虫任务队列后端抽象基类。"""

    @abstractmethod
    async def enqueue(
        self,
        project_id: str,
        tasks: list[QueueTask],
        priority: int = 5,
    ) -> list[str]:
        pass

    @abstractmethod
    async def dequeue(
        self,
        project_id: str,
        consumer: str,
        count: int = 50,
        timeout_ms: int = 5000,
    ) -> list[QueueTask]:
        pass

    @abstractmethod
    async def ack(
        self,
        project_id: str,
        msg_ids: list[str],
    ) -> int:
        pass

    @abstractmethod
    async def reclaim(
        self,
        project_id: str,
        min_idle_ms: int = 300000,
        count: int = 100,
    ) -> list[ReclaimedTask]:
        pass

    @abstractmethod
    async def stats(self, project_id: str) -> QueueStats:
        pass

    @abstractmethod
    async def get_queue_metrics(
        self,
        project_id: str,
        priority: int,
    ) -> QueueMetrics:
        pass

    @abstractmethod
    async def ensure_queues(self, project_id: str) -> bool:
        pass

    @abstractmethod
    async def clear_queues(self, project_id: str) -> bool:
        pass

    @abstractmethod
    async def get_queue_length(
        self,
        project_id: str,
        priority: int | None = None,
    ) -> int:
        pass

    @abstractmethod
    async def get_pending_count(
        self,
        project_id: str,
        priority: int | None = None,
    ) -> int:
        pass

    @abstractmethod
    async def move_to_dead_letter(
        self,
        project_id: str,
        tasks: list[QueueTask],
    ) -> int:
        pass

    @abstractmethod
    async def get_dead_letter_count(self, project_id: str) -> int:
        pass


# 后端实例缓存
_queue_backend_instance: CrawlQueueBackend | None = None


def get_queue_backend() -> CrawlQueueBackend:
    """返回 Redis Streams 爬虫队列后端。"""
    global _queue_backend_instance

    if _queue_backend_instance is not None:
        return _queue_backend_instance

    _redis_backend_required("CRAWL_BACKEND")

    from antcode_core.application.services.crawl.backends.redis_queue import RedisCrawlQueueBackend

    _queue_backend_instance = RedisCrawlQueueBackend()

    return _queue_backend_instance


def reset_queue_backend() -> None:
    """重置队列后端实例（用于测试）"""
    global _queue_backend_instance
    _queue_backend_instance = None
