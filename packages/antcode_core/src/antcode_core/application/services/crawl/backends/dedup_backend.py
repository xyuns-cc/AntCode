"""Redis 去重存储后端抽象基类。"""

from abc import ABC, abstractmethod

from antcode_core.application.services.crawl.backends.base import _redis_backend_required


class DedupStore(ABC):
    """URL 去重存储抽象基类

    定义去重操作的标准接口，支持：
    - 单个指纹的存在性检查
    - 单个指纹的添加
    - 批量指纹的检查和添加
    - 去重集合大小查询

    Requirements: 2.1, 2.2, 2.3
    """

    @abstractmethod
    async def exists(self, project_id: str, fingerprint: str) -> bool:
        """检查指纹是否存在

        Args:
            project_id: 项目 ID
            fingerprint: URL 指纹

        Returns:
            True 表示可能存在，False 表示一定不存在

        Requirements: 2.4
        """
        pass

    @abstractmethod
    async def add(self, project_id: str, fingerprint: str) -> bool:
        """添加指纹

        Args:
            project_id: 项目 ID
            fingerprint: URL 指纹

        Returns:
            True 表示新添加成功，False 表示已存在

        Requirements: 2.5
        """
        pass

    @abstractmethod
    async def add_many(self, project_id: str, fingerprints: list[str]) -> list[bool]:
        """批量添加指纹

        Args:
            project_id: 项目 ID
            fingerprints: URL 指纹列表

        Returns:
            布尔值列表，与输入列表一一对应，True 表示新添加，False 表示已存在

        Requirements: 2.6
        """
        pass

    @abstractmethod
    async def exists_many(self, project_id: str, fingerprints: list[str]) -> list[bool]:
        """批量检查指纹是否存在

        Args:
            project_id: 项目 ID
            fingerprints: URL 指纹列表

        Returns:
            布尔值列表，与输入列表一一对应
        """
        pass

    @abstractmethod
    async def size(self, project_id: str) -> int:
        """获取去重集合大小

        Args:
            project_id: 项目 ID

        Returns:
            当前元素数量

        Requirements: 2.7
        """
        pass

    @abstractmethod
    async def clear(self, project_id: str) -> bool:
        """清空去重集合

        Args:
            project_id: 项目 ID

        Returns:
            是否成功
        """
        pass

    @abstractmethod
    async def ensure_store(
        self,
        project_id: str,
        capacity: int = 1000000,
        error_rate: float = 0.001,
    ) -> bool:
        """确保去重存储存在

        Args:
            project_id: 项目 ID
            capacity: 预期容量
            error_rate: 误判率（仅 Bloom Filter 实现使用）

        Returns:
            是否成功
        """
        pass


# 后端实例缓存
_dedup_store_instance: DedupStore | None = None


def get_dedup_store() -> DedupStore:
    """返回 Redis 去重存储。"""
    global _dedup_store_instance

    if _dedup_store_instance is not None:
        return _dedup_store_instance

    _redis_backend_required("CRAWL_BACKEND", "DEDUP_BACKEND")

    from antcode_core.application.services.crawl.backends.redis_dedup import RedisDedupStore

    _dedup_store_instance = RedisDedupStore()

    return _dedup_store_instance


def reset_dedup_store() -> None:
    """重置去重存储实例（用于测试）"""
    global _dedup_store_instance
    _dedup_store_instance = None
