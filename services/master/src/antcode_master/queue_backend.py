"""任务队列后端抽象层

定义 TaskQueueBackend Protocol 和 QueuedTask 数据类，
支持内存队列和 Redis 队列的统一接口。
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from time import time
from typing import Protocol, runtime_checkable

from loguru import logger

from antcode_core.common.serialization import Serializer


@dataclass
class QueuedTask:
    """队列任务数据类

    用于在队列中存储和传输任务信息。
    支持 JSON 序列化/反序列化以便 Redis 存储。
    """

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
    """任务队列后端协议

    定义任务队列的统一接口，支持内存队列和 Redis 队列实现。
    """

    async def start(self):
        """启动队列"""
        ...

    async def stop(self):
        """停止队列"""
        ...

    async def enqueue(self, task_id, project_id, priority, data, project_type="rule"):
        """入队任务

        Args:
            task_id: 任务唯一标识
            project_id: 项目ID
            priority: 优先级（数值越小优先级越高）
            data: 任务数据
            project_type: 项目类型

        Returns:
            是否成功入队
        """
        ...

    async def dequeue(self, timeout=None):
        """出队任务

        Args:
            timeout: 超时时间（秒），None 表示非阻塞

        Returns:
            任务数据或 None（队列为空时）
        """
        ...

    async def cancel(self, task_id):
        """取消任务

        Args:
            task_id: 任务唯一标识

        Returns:
            是否成功取消
        """
        ...

    async def update_priority(self, task_id, new_priority):
        """更新任务优先级

        Args:
            task_id: 任务唯一标识
            new_priority: 新优先级

        Returns:
            是否成功更新
        """
        ...

    async def get_status(self):
        """获取队列状态

        Returns:
            包含队列深度、后端类型等信息的字典
        """
        ...

    def contains(self, task_id):
        """检查任务是否在队列中

        Args:
            task_id: 任务唯一标识

        Returns:
            是否存在
        """
        ...

    def size(self):
        """获取队列大小

        Returns:
            队列中的任务数量
        """
        ...


class BaseQueueBackend(ABC):
    """队列后端抽象基类

    提供队列后端的公共逻辑，包括：
    - 统计信息管理
    - 运行状态管理
    - 统一的日志格式

    子类需要实现所有抽象方法。
    """

    # 后端类型名称，子类应覆盖
    BACKEND_TYPE = "base"

    def __init__(self):
        """初始化基类"""
        self._running = False
        self._stats = {
            "enqueued": 0,
            "dequeued": 0,
            "cancelled": 0,
            "priority_updates": 0,
        }

    def _update_stats(self, operation, count=1):
        """更新统计信息

        Args:
            operation: 操作类型 (enqueued, dequeued, cancelled, priority_updates)
            count: 增加的数量，默认为 1
        """
        if operation in self._stats:
            self._stats[operation] += count

    def _log_operation(self, operation, task_id, **kwargs):
        """统一的操作日志记录

        Args:
            operation: 操作类型
            task_id: 任务ID
            **kwargs: 额外的日志参数
        """
        extra_info = ", ".join(f"{k}: {v}" for k, v in kwargs.items()) if kwargs else ""
        if extra_info:
            logger.debug(f"[{self.BACKEND_TYPE}] {operation} - 任务 {task_id}, {extra_info}")
        else:
            logger.debug(f"[{self.BACKEND_TYPE}] {operation} - 任务 {task_id}")

    def _log_warning(self, message, task_id=None):
        """统一的警告日志记录

        Args:
            message: 警告消息
            task_id: 任务ID（可选）
        """
        if task_id:
            logger.warning(f"[{self.BACKEND_TYPE}] {message} - 任务 {task_id}")
        else:
            logger.warning(f"[{self.BACKEND_TYPE}] {message}")

    def _log_error(self, message, error=None):
        """统一的错误日志记录

        Args:
            message: 错误消息
            error: 异常对象（可选）
        """
        if error:
            logger.error(f"[{self.BACKEND_TYPE}] {message}: {error}")
        else:
            logger.error(f"[{self.BACKEND_TYPE}] {message}")

    def get_stats(self):
        """获取统计信息副本

        Returns:
            统计信息字典的副本
        """
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
    """获取配置的队列后端类型

    从环境变量 QUEUE_BACKEND 读取，默认为 "memory"。

    Returns:
        队列后端类型: "memory" 或 "redis"
    """
    return os.getenv("QUEUE_BACKEND", "memory").lower()


def get_queue_backend():
    """获取队列后端实例（工厂函数）

    根据 QUEUE_BACKEND 环境变量选择实现：
    - "memory" 或未设置: 使用 MemoryQueueBackend
    - "redis": 使用 RedisQueueBackend（需要设置 REDIS_URL）

    Returns:
        TaskQueueBackend 实例

    Raises:
        ValueError: 当 QUEUE_BACKEND=redis 但 REDIS_URL 未设置时
        ValueError: 当 QUEUE_BACKEND 值无效时
    """
    global _queue_backend_instance

    if _queue_backend_instance is not None:
        return _queue_backend_instance

    backend_type = get_queue_backend_type()

    if backend_type == "memory" or backend_type == "":
        from antcode_core.application.services.scheduler.memory_queue import MemoryQueueBackend

        _queue_backend_instance = MemoryQueueBackend()
        logger.info("使用内存任务队列后端 (MemoryQueueBackend)")

    elif backend_type == "redis":
        redis_url = os.getenv("REDIS_URL", "").strip()
        if not redis_url:
            raise ValueError("QUEUE_BACKEND=redis 时必须设置 REDIS_URL 环境变量")
        from antcode_core.application.services.scheduler.redis_queue import RedisQueueBackend

        _queue_backend_instance = RedisQueueBackend(redis_url)
        logger.info("使用 Redis 任务队列后端 (RedisQueueBackend)")

    else:
        raise ValueError(f"无效的 QUEUE_BACKEND 值: {backend_type}，支持的值: memory, redis")

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
