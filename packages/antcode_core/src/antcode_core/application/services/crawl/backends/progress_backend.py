"""进度存储后端抽象基类

定义进度存储的抽象接口，支持 Redis Hash 和内存两种实现。

Requirements: 3.1, 3.2, 3.3
"""

import os
from abc import ABC, abstractmethod
from typing import Any


class ProgressStore(ABC):
    """批次进度存储抽象基类

    定义进度存储操作的标准接口，支持：
    - 进度数据的获取和设置
    - Worker 活跃状态注册和查询
    - 检查点保存和加载

    Requirements: 3.1, 3.2, 3.3
    """

    @abstractmethod
    async def get_progress(
        self,
        project_id: str,
        batch_id: str,
    ) -> dict[str, Any] | None:
        """获取批次进度

        Args:
            project_id: 项目 ID
            batch_id: 批次 ID

        Returns:
            进度字典，不存在时返回 None

        Requirements: 3.4
        """
        pass

    @abstractmethod
    async def set_progress(
        self,
        project_id: str,
        batch_id: str,
        data: dict[str, Any],
    ) -> bool:
        """设置批次进度

        Args:
            project_id: 项目 ID
            batch_id: 批次 ID
            data: 进度数据字典

        Returns:
            是否成功

        Requirements: 3.5
        """
        pass

    @abstractmethod
    async def update_progress(
        self,
        project_id: str,
        batch_id: str,
        updates: dict[str, Any],
    ) -> bool:
        """增量更新批次进度

        Args:
            project_id: 项目 ID
            batch_id: 批次 ID
            updates: 要更新的字段字典

        Returns:
            是否成功
        """
        pass

    @abstractmethod
    async def increment_progress(
        self,
        project_id: str,
        batch_id: str,
        field: str,
        amount: int = 1,
    ) -> int:
        """原子增加进度字段值

        Args:
            project_id: 项目 ID
            batch_id: 批次 ID
            field: 字段名
            amount: 增加量

        Returns:
            更新后的值
        """
        pass

    @abstractmethod
    async def register_worker(
        self,
        project_id: str,
        batch_id: str,
        worker_id: str,
        ttl: int = 60,
    ) -> bool:
        """注册活跃 Worker

        Args:
            project_id: 项目 ID
            batch_id: 批次 ID
            worker_id: Worker ID
            ttl: 存活时间（秒）

        Returns:
            是否成功

        Requirements: 3.6
        """
        pass

    @abstractmethod
    async def get_active_workers(
        self,
        project_id: str,
        batch_id: str,
    ) -> list[str]:
        """获取活跃 Worker 列表

        自动过滤超时的 Worker。

        Args:
            project_id: 项目 ID
            batch_id: 批次 ID

        Returns:
            活跃 Worker ID 列表

        Requirements: 3.7
        """
        pass

    @abstractmethod
    async def unregister_worker(
        self,
        project_id: str,
        batch_id: str,
        worker_id: str,
    ) -> bool:
        """注销 Worker

        Args:
            project_id: 项目 ID
            batch_id: 批次 ID
            worker_id: Worker ID

        Returns:
            是否成功
        """
        pass

    @abstractmethod
    async def save_checkpoint(
        self,
        project_id: str,
        batch_id: str,
        checkpoint_data: dict[str, Any],
    ) -> bool:
        """保存检查点

        Args:
            project_id: 项目 ID
            batch_id: 批次 ID
            checkpoint_data: 检查点数据

        Returns:
            是否成功
        """
        pass

    @abstractmethod
    async def load_checkpoint(
        self,
        project_id: str,
        batch_id: str,
    ) -> dict[str, Any] | None:
        """加载检查点

        Args:
            project_id: 项目 ID
            batch_id: 批次 ID

        Returns:
            检查点数据，不存在时返回 None
        """
        pass

    @abstractmethod
    async def delete_checkpoint(
        self,
        project_id: str,
        batch_id: str,
    ) -> bool:
        """删除检查点

        Args:
            project_id: 项目 ID
            batch_id: 批次 ID

        Returns:
            是否成功
        """
        pass

    @abstractmethod
    async def clear(
        self,
        project_id: str,
        batch_id: str,
    ) -> bool:
        """清除批次所有进度数据

        包括进度、检查点和 Worker 注册信息。

        Args:
            project_id: 项目 ID
            batch_id: 批次 ID

        Returns:
            是否成功
        """
        pass


# 后端实例缓存
_progress_store_instance: ProgressStore | None = None


def get_progress_store() -> ProgressStore:
    """工厂方法：根据配置返回进度存储实现

    通过环境变量 CRAWL_BACKEND 或 PROGRESS_BACKEND 配置后端类型：
    - "memory": 内存实现（默认）
    - "redis": Redis Hash 实现

    Returns:
        ProgressStore 实例

    Raises:
        ValueError: 无效的后端类型

    Requirements: 3.1, 3.2, 3.3
    """
    global _progress_store_instance

    if _progress_store_instance is not None:
        return _progress_store_instance

    # 优先使用 CRAWL_BACKEND，其次使用 PROGRESS_BACKEND
    backend_type = os.getenv("CRAWL_BACKEND") or os.getenv("PROGRESS_BACKEND", "memory")
    backend_type = backend_type.lower().strip()

    if backend_type == "redis":
        from antcode_core.application.services.crawl.backends.redis_progress import RedisProgressStore
        _progress_store_instance = RedisProgressStore()
    elif backend_type == "memory":
        from antcode_core.application.services.crawl.backends.memory_progress import InMemoryProgressStore
        _progress_store_instance = InMemoryProgressStore()
    else:
        raise ValueError(f"Unknown progress backend: {backend_type}")

    return _progress_store_instance


def reset_progress_store() -> None:
    """重置进度存储实例（用于测试）"""
    global _progress_store_instance
    _progress_store_instance = None
