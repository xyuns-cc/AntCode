"""爬虫 Redis 队列后端抽象基类。"""

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from antcode_core.common.settings_ref import current_settings

ALLOWED_PRIORITIES = frozenset({0, 5, 9})


def _redis_backend_required(*env_names: str) -> None:
    for env_name in env_names:
        value = os.getenv(env_name, "").strip().lower()
        if value and value != "redis":
            raise ValueError("爬虫队列只能使用 Redis")
    if current_settings().CRAWL_BACKEND.strip().lower() != "redis":
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
        if not isinstance(data, dict):
            raise ValueError("queue task 必须是对象")
        url = data.get("url")
        headers = data.get("headers")
        if headers is None:
            headers = {}
        if not isinstance(url, str) or not url.strip():
            raise ValueError("queue task url 必须是非空字符串")
        if not isinstance(headers, dict):
            raise ValueError("queue task headers 必须是对象")
        priority = _non_negative_int(data.get("priority", 5), "priority")
        if priority not in ALLOWED_PRIORITIES:
            raise ValueError("queue task priority 无效")
        parent_url = _optional_text_field(data.get("parent_url"), "parent_url")
        return cls(
            msg_id=msg_id,
            url=url,
            method=_text_field(data.get("method", "GET"), "method"),
            headers=headers,
            depth=_non_negative_int(data.get("depth", 0), "depth"),
            priority=priority,
            retry_count=_non_negative_int(data.get("retry_count", 0), "retry_count"),
            parent_url=parent_url,
            batch_id=_text_field(data.get("batch_id", ""), "batch_id"),
            project_id=_text_field(data.get("project_id", ""), "project_id"),
            status=_text_field(data.get("status", "pending"), "status"),
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


@dataclass(frozen=True)
class QueueProjectDiscovery:
    """Project streams eligible for takeover plus explicit scan failures."""

    project_ids: tuple[str, ...] = ()
    failures: tuple[str, ...] = ()


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

    async def enqueue_unique(
        self,
        project_id: str,
        tasks: list[QueueTask],
        *,
        fingerprints: list[str],
        priority: int = 5,
    ) -> list[str | None]:
        """原子判重并入队；实现必须保持返回值与输入位置一一对应。"""
        raise NotImplementedError("queue backend 不支持原子判重入队")

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
        priority: int,
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
    async def list_project_ids(self) -> list[str]:
        """列出当前 Redis 中存在 Crawl Stream 的项目。"""
        pass

    async def discover_projects(self) -> QueueProjectDiscovery:
        """Discover recoverable projects without hiding per-project failures."""
        return QueueProjectDiscovery(project_ids=tuple(await self.list_project_ids()))

    @abstractmethod
    async def requeue_claimed(self, project_id: str, task: QueueTask) -> str | None:
        """原子重新入队并 ACK 已认领的源消息。"""
        pass

    @abstractmethod
    async def dead_letter_claimed(self, project_id: str, task: QueueTask) -> str | None:
        """原子移入死信队列并 ACK 已认领的源消息。"""
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


def _non_negative_int(value: object, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"queue task {field_name} 必须是非负整数")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, float) and value.is_integer():
        parsed = int(value)
    elif isinstance(value, str) and value.isdigit():
        parsed = int(value)
    else:
        raise ValueError(f"queue task {field_name} 必须是非负整数")
    if parsed < 0:
        raise ValueError(f"queue task {field_name} 必须是非负整数")
    return parsed


def _text_field(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"queue task {field_name} 必须是字符串")
    return value


def _optional_text_field(value: object, field_name: str) -> str | None:
    if value is None or value == "":
        return None
    return _text_field(value, field_name)
