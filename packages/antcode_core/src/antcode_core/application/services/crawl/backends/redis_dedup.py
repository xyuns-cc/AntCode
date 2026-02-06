"""Redis 去重存储实现

基于 Redis Bloom Filter 实现的去重存储，支持高效的大规模 URL 去重。

Requirements: 2.2, 2.4, 2.5, 2.6, 2.7
"""

from loguru import logger

from antcode_core.application.services.crawl.backends.dedup_backend import DedupStore
from antcode_core.infrastructure.redis.bloom_client import BloomFilterClient

# Redis 键前缀
DEDUP_KEY_PREFIX = "rule"
DEDUP_KEY_SUFFIX = "dedup"


def get_dedup_key(project_id: str) -> str:
    """获取去重过滤器的 Redis 键名

    Args:
        project_id: 项目 ID

    Returns:
        Redis 键名，格式: rule:{project_id}:dedup
    """
    return f"{DEDUP_KEY_PREFIX}:{project_id}:{DEDUP_KEY_SUFFIX}"


class RedisDedupStore(DedupStore):
    """Redis Bloom Filter 去重存储实现

    基于 Redis Bloom Filter 实现，支持：
    - 高效的大规模去重（百万级）
    - 可配置的误判率
    - 自动扩展

    注意：
    - 需要 Redis 安装 RedisBloom 模块
    - 如果模块不可用，会降级使用 Set 实现

    Requirements: 2.2, 2.4, 2.5, 2.6, 2.7
    """

    # 默认配置
    DEFAULT_CAPACITY = 1000000  # 默认容量 100 万
    DEFAULT_ERROR_RATE = 0.001  # 默认误判率 0.1%

    def __init__(self, bloom_client: BloomFilterClient = None):
        """初始化 Redis 去重存储

        Args:
            bloom_client: Bloom Filter 客户端，为 None 时自动创建
        """
        self._bloom_client = bloom_client or BloomFilterClient()

    async def exists(self, project_id: str, fingerprint: str) -> bool:
        """检查指纹是否存在

        Requirements: 2.4
        """
        key = get_dedup_key(project_id)
        result = await self._bloom_client.bf_exists(key, fingerprint)

        logger.debug(
            f"检查去重: project={project_id}, "
            f"fingerprint={fingerprint[:8]}..., exists={result}"
        )

        return result

    async def add(self, project_id: str, fingerprint: str) -> bool:
        """添加指纹

        Returns:
            True 表示新添加成功，False 表示已存在

        Requirements: 2.5
        """
        key = get_dedup_key(project_id)
        result = await self._bloom_client.bf_add(key, fingerprint)

        if result:
            logger.debug(
                f"添加去重: project={project_id}, "
                f"fingerprint={fingerprint[:8]}..."
            )

        return result

    async def add_many(self, project_id: str, fingerprints: list[str]) -> list[bool]:
        """批量添加指纹

        Returns:
            布尔值列表，与输入列表一一对应

        Requirements: 2.6
        """
        if not fingerprints:
            return []

        key = get_dedup_key(project_id)
        results = await self._bloom_client.bf_madd(key, fingerprints)

        added_count = sum(1 for r in results if r)
        logger.debug(
            f"批量添加去重: project={project_id}, "
            f"total={len(fingerprints)}, added={added_count}"
        )

        return results

    async def exists_many(self, project_id: str, fingerprints: list[str]) -> list[bool]:
        """批量检查指纹是否存在"""
        if not fingerprints:
            return []

        key = get_dedup_key(project_id)
        results = await self._bloom_client.bf_mexists(key, fingerprints)

        exists_count = sum(1 for r in results if r)
        logger.debug(
            f"批量检查去重: project={project_id}, "
            f"total={len(fingerprints)}, exists={exists_count}"
        )

        return results

    async def size(self, project_id: str) -> int:
        """获取去重集合大小

        Requirements: 2.7
        """
        key = get_dedup_key(project_id)
        return await self._bloom_client.get_item_count(key)

    async def clear(self, project_id: str) -> bool:
        """清空去重集合"""
        key = get_dedup_key(project_id)
        result = await self._bloom_client.delete_filter(key)

        logger.info(f"清空去重存储: project={project_id}")

        return result

    async def ensure_store(
        self,
        project_id: str,
        capacity: int = 1000000,
        error_rate: float = 0.001,
    ) -> bool:
        """确保去重存储存在"""
        key = get_dedup_key(project_id)
        capacity = capacity or self.DEFAULT_CAPACITY
        error_rate = error_rate or self.DEFAULT_ERROR_RATE

        return await self._bloom_client.ensure_filter(key, capacity, error_rate)

    async def get_filter_info(self, project_id: str) -> dict:
        """获取去重过滤器信息

        Args:
            project_id: 项目 ID

        Returns:
            过滤器信息字典
        """
        key = get_dedup_key(project_id)
        info = await self._bloom_client.bf_info(key)

        return {
            "project_id": project_id,
            "capacity": info.capacity,
            "size": info.size,
            "num_items": info.num_items_inserted,
            "num_filters": info.num_filters,
            "expansion_rate": info.expansion_rate,
        }

    async def recreate_store(
        self,
        project_id: str,
        capacity: int = None,
        error_rate: float = None,
    ) -> bool:
        """清空并重建去重存储

        Args:
            project_id: 项目 ID
            capacity: 新容量
            error_rate: 新误判率

        Returns:
            是否成功
        """
        key = get_dedup_key(project_id)
        capacity = capacity or self.DEFAULT_CAPACITY
        error_rate = error_rate or self.DEFAULT_ERROR_RATE

        result = await self._bloom_client.clear_filter(key, capacity, error_rate)

        logger.info(
            f"重建去重存储: project={project_id}, "
            f"capacity={capacity}, error_rate={error_rate}"
        )

        return result
