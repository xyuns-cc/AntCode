"""任务队列后端抽象层。"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from time import time
from typing import Protocol, runtime_checkable

from loguru import logger

from antcode_core.common.config import settings
from antcode_core.common.serialization import Serializer


@dataclass
class QueuedTask:
    """队列任务。"""

    task_id: str
    project_id: str
    project_type: str
    priority: int
    enqueue_time: float = field(default_factory=time)
    data: dict = field(default_factory=dict)

    def to_json(self):
        """序列化为 JSON 字符串（使用 ujson 高性能序列化）"""
        return Serializer.to_json(asdict(self))

    @classmethod
    def from_json(cls, json_str):
        """从 JSON 字符串反序列化（使用 ujson 高性能反序列化）"""
        data = Serializer.from_json(json_str)
        return cls(**data)

    def to_dict(self):
        """转换为字典"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        """从字典创建"""
        return cls(**data)


@runtime_checkable
class TaskQueueBackend(Protocol):
    """任务队列后端协议。"""

    async def start(self): ...

    async def stop(self): ...

    async def enqueue(self, task_id, project_id, priority, data, project_type="rule"): ...

    async def dequeue(self, timeout=None): ...

    async def cancel(self, task_id): ...

    async def update_priority(self, task_id, new_priority): ...

    async def get_status(self): ...

    def contains(self, task_id): ...

    def size(self): ...


class BaseQueueBackend(ABC):
    """队列后端抽象基类。"""

    BACKEND_TYPE = "base"

    def __init__(self):
        self._running = False
        self._stats = {
            "enqueued": 0,
            "dequeued": 0,
            "cancelled": 0,
            "priority_updates": 0,
        }

    def _update_stats(self, operation, count=1):
        if operation in self._stats:
            self._stats[operation] += count

    def _log_operation(self, operation, task_id, **kwargs):
        extra_info = ", ".join(f"{k}: {v}" for k, v in kwargs.items()) if kwargs else ""
        if extra_info:
            logger.debug(f"[{self.BACKEND_TYPE}] {operation} - 任务 {task_id}, {extra_info}")
        else:
            logger.debug(f"[{self.BACKEND_TYPE}] {operation} - 任务 {task_id}")

    def _log_warning(self, message, task_id=None):
        if task_id:
            logger.warning(f"[{self.BACKEND_TYPE}] {message} - 任务 {task_id}")
        else:
            logger.warning(f"[{self.BACKEND_TYPE}] {message}")

    def _log_error(self, message, error=None):
        if error:
            logger.error(f"[{self.BACKEND_TYPE}] {message}: {error}")
        else:
            logger.error(f"[{self.BACKEND_TYPE}] {message}")

    def get_stats(self):
        return self._stats.copy()

    def is_running(self):
        """检查队列是否正在运行

        Returns:
            是否正在运行
        """
        return self._running

    @abstractmethod
    async def start(self):
        """启动队列"""
        pass

    @abstractmethod
    async def stop(self):
        """停止队列"""
        pass

    @abstractmethod
    async def enqueue(self, task_id, project_id, priority, data, project_type="rule"):
        """入队任务"""
        pass

    @abstractmethod
    async def dequeue(self, timeout=None):
        """出队任务"""
        pass

    @abstractmethod
    async def cancel(self, task_id):
        """取消任务"""
        pass

    @abstractmethod
    async def update_priority(self, task_id, new_priority):
        """更新任务优先级"""
        pass

    @abstractmethod
    async def get_status(self):
        """获取队列状态"""
        pass

    @abstractmethod
    def contains(self, task_id):
        """检查任务是否在队列中"""
        pass

    @abstractmethod
    def size(self):
        """获取队列大小"""
        pass


# ============== 队列后端工厂 ==============

# 全局队列后端实例（单例）
_queue_backend_instance = None


def get_queue_backend_type():
    """获取队列后端类型。"""
    configured = os.getenv("QUEUE_BACKEND", "").strip().lower()
    if configured and configured != "redis":
        raise ValueError("任务控制队列只能使用 Redis")
    return "redis"


def get_queue_backend():
    """获取 Redis 队列后端实例。"""
    global _queue_backend_instance

    if _queue_backend_instance is not None:
        return _queue_backend_instance

    get_queue_backend_type()
    redis_url = settings.REDIS_URL.strip()
    if not redis_url:
        raise ValueError("Redis 控制队列必须设置 REDIS_URL")
    from antcode_core.application.services.scheduler.redis_queue import RedisQueueBackend

    _queue_backend_instance = RedisQueueBackend(redis_url)
    logger.info("使用 Redis 任务队列后端 (RedisQueueBackend)")

    return _queue_backend_instance


def reset_queue_backend():
    """重置队列后端实例（主要用于测试）"""
    global _queue_backend_instance
    _queue_backend_instance = None


async def init_queue_backend():
    """初始化并启动队列后端

    Returns:
        已启动的 TaskQueueBackend 实例
    """
    backend = get_queue_backend()
    await backend.start()
    return backend


async def shutdown_queue_backend():
    """关闭队列后端"""
    global _queue_backend_instance
    if _queue_backend_instance is not None:
        await _queue_backend_instance.stop()
        _queue_backend_instance = None
