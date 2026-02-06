"""爬虫队列后端抽象基类

定义队列后端的抽象接口，支持 Redis 和内存两种实现。

Requirements: 1.1, 1.2, 1.3
"""

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


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
    """爬虫任务队列后端抽象基类

    定义队列操作的标准接口，支持：
    - 多优先级队列入队和出队
    - 任务确认和超时回收
    - 队列统计信息查询

    Requirements: 1.1, 1.2, 1.3
    """

    @abstractmethod
    async def enqueue(
        self,
        project_id: str,
        tasks: list[QueueTask],
        priority: int = 5,
    ) -> list[str]:
        """任务入队

        Args:
            project_id: 项目 ID
            tasks: 任务列表
            priority: 优先级 (0=高, 5=普通, 9=低)

        Returns:
            消息 ID 列表

        Requirements: 1.4
        """
        pass

    @abstractmethod
    async def dequeue(
        self,
        project_id: str,
        consumer: str,
        count: int = 50,
        timeout_ms: int = 5000,
    ) -> list[QueueTask]:
        """任务出队

        按优先级顺序获取任务，优先返回高优先级队列中的任务。

        Args:
            project_id: 项目 ID
            consumer: 消费者标识
            count: 获取数量
            timeout_ms: 阻塞等待毫秒数

        Returns:
            任务列表

        Requirements: 1.5
        """
        pass

    @abstractmethod
    async def ack(
        self,
        project_id: str,
        msg_ids: list[str],
    ) -> int:
        """确认任务完成

        Args:
            project_id: 项目 ID
            msg_ids: 消息 ID 列表

        Returns:
            确认成功的数量

        Requirements: 1.6
        """
        pass

    @abstractmethod
    async def reclaim(
        self,
        project_id: str,
        min_idle_ms: int = 300000,
        count: int = 100,
    ) -> list[ReclaimedTask]:
        """回收超时任务

        扫描处理中的任务，将超时任务回收并增加重试计数。

        Args:
            project_id: 项目 ID
            min_idle_ms: 最小空闲时间（毫秒）
            count: 最大回收数量

        Returns:
            回收的任务列表（包含重试计数）

        Requirements: 1.7
        """
        pass

    @abstractmethod
    async def stats(self, project_id: str) -> QueueStats:
        """获取队列统计信息

        Args:
            project_id: 项目 ID

        Returns:
            队列统计信息

        Requirements: 1.8
        """
        pass

    @abstractmethod
    async def get_queue_metrics(
        self,
        project_id: str,
        priority: int,
    ) -> QueueMetrics:
        """获取单个优先级队列的指标

        Args:
            project_id: 项目 ID
            priority: 优先级

        Returns:
            QueueMetrics 对象
        """
        pass

    @abstractmethod
    async def ensure_queues(self, project_id: str) -> bool:
        """确保项目队列存在

        Args:
            project_id: 项目 ID

        Returns:
            是否成功
        """
        pass

    @abstractmethod
    async def clear_queues(self, project_id: str) -> bool:
        """清空项目队列

        Args:
            project_id: 项目 ID

        Returns:
            是否成功
        """
        pass

    @abstractmethod
    async def get_queue_length(
        self,
        project_id: str,
        priority: int | None = None,
    ) -> int:
        """获取队列长度

        Args:
            project_id: 项目 ID
            priority: 优先级，None 表示所有优先级总和

        Returns:
            队列长度
        """
        pass

    @abstractmethod
    async def get_pending_count(
        self,
        project_id: str,
        priority: int | None = None,
    ) -> int:
        """获取待处理（处理中）消息数量

        Args:
            project_id: 项目 ID
            priority: 优先级，None 表示所有优先级总和

        Returns:
            待处理消息数量
        """
        pass

    @abstractmethod
    async def move_to_dead_letter(
        self,
        project_id: str,
        tasks: list[QueueTask],
    ) -> int:
        """将任务移入死信队列

        Args:
            project_id: 项目 ID
            tasks: 任务列表

        Returns:
            移入数量
        """
        pass

    @abstractmethod
    async def get_dead_letter_count(self, project_id: str) -> int:
        """获取死信队列消息数量

        Args:
            project_id: 项目 ID

        Returns:
            死信队列消息数量
        """
        pass


# 后端实例缓存
_queue_backend_instance: CrawlQueueBackend | None = None


def get_queue_backend() -> CrawlQueueBackend:
    """工厂方法：根据配置返回队列后端实现

    通过环境变量 CRAWL_BACKEND 配置后端类型：
    - "memory": 内存队列实现（默认）
    - "redis": Redis Streams 实现

    Returns:
        CrawlQueueBackend 实例

    Raises:
        ValueError: 无效的后端类型

    Requirements: 1.1, 1.2, 1.3
    """
    global _queue_backend_instance

    if _queue_backend_instance is not None:
        return _queue_backend_instance

    backend_type = os.getenv("CRAWL_BACKEND", "memory").lower().strip()

    if backend_type == "redis":
        from antcode_core.application.services.crawl.backends.redis_queue import RedisCrawlQueueBackend
        _queue_backend_instance = RedisCrawlQueueBackend()
    elif backend_type == "memory":
        from antcode_core.application.services.crawl.backends.memory_queue import InMemoryCrawlQueueBackend
        _queue_backend_instance = InMemoryCrawlQueueBackend()
    else:
        raise ValueError(f"Unknown queue backend: {backend_type}")

    return _queue_backend_instance


def reset_queue_backend() -> None:
    """重置队列后端实例（用于测试）"""
    global _queue_backend_instance
    _queue_backend_instance = None
