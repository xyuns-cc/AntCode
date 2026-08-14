"""基于标准 Redis Set 的精确 URL 去重存储。"""

from loguru import logger

from antcode_core.application.services.crawl.backends.dedup_backend import (
    DedupStore,
    DedupStoreCapabilities,
)
from antcode_core.application.services.crawl.backends.redis_keys import (
    crawl_dedup_key,
    crawl_project_deleted_key,
)
from antcode_core.infrastructure.redis.client import get_redis_client
from antcode_core.infrastructure.redis.control_plane import redis_namespace

_ADD_ACTIVE = """
if redis.call('EXISTS', KEYS[2]) == 1 then
    return redis.error_reply('CRAWL_PROJECT_DELETED')
end
return redis.call('SADD', KEYS[1], ARGV[1])
"""

_ADD_MANY_ACTIVE = """
if redis.call('EXISTS', KEYS[2]) == 1 then
    return redis.error_reply('CRAWL_PROJECT_DELETED')
end
local results = {}
for index = 1, #ARGV do
    table.insert(results, redis.call('SADD', KEYS[1], ARGV[index]))
end
return results
"""


def get_dedup_key(project_id: str, namespace: str | None = None) -> str:
    """获取去重过滤器的 Redis 键名

    Args:
        project_id: 项目 ID

    Returns:
        带 namespace 与项目 hash tag 的 Redis 键名
    """
    return crawl_dedup_key(project_id, namespace)


class RedisDedupStore(DedupStore):
    """精确去重，不依赖生产 Compose 未提供的 RedisBloom 模块。"""

    def __init__(
        self,
        redis_client=None,
        namespace: str | None = None,
    ):
        """初始化 Redis 去重存储

        Args:
            redis_client: Redis 客户端，为 None 时自动创建
        """
        self._redis = redis_client
        self._namespace = redis_namespace(namespace)

    async def _get_client(self):
        if self._redis is None:
            self._redis = await get_redis_client()
        return self._redis

    def _key(self, project_id: str) -> str:
        return get_dedup_key(project_id, self._namespace)

    def _deleted_fence_key(self, project_id: str) -> str:
        return crawl_project_deleted_key(project_id, self._namespace)

    async def exists(self, project_id: str, fingerprint: str) -> bool:
        """检查指纹是否存在

        Requirements: 2.4
        """
        key = self._key(project_id)
        result = await (await self._get_client()).sismember(key, fingerprint)

        logger.debug(f"检查去重: project={project_id}, fingerprint={fingerprint[:8]}..., exists={result}")

        return bool(result)

    async def add(self, project_id: str, fingerprint: str) -> bool:
        """添加指纹

        Returns:
            True 表示新添加成功，False 表示已存在

        Requirements: 2.5
        """
        key = self._key(project_id)
        result = await (await self._get_client()).eval(
            _ADD_ACTIVE,
            2,
            key,
            self._deleted_fence_key(project_id),
            fingerprint,
        )

        if result:
            logger.debug(f"添加去重: project={project_id}, fingerprint={fingerprint[:8]}...")

        return bool(result)

    async def add_many(self, project_id: str, fingerprints: list[str]) -> list[bool]:
        """批量添加指纹

        Returns:
            布尔值列表，与输入列表一一对应

        Requirements: 2.6
        """
        if not fingerprints:
            return []

        key = self._key(project_id)
        client = await self._get_client()
        results = await client.eval(
            _ADD_MANY_ACTIVE,
            2,
            key,
            self._deleted_fence_key(project_id),
            *fingerprints,
        )

        added_count = sum(1 for r in results if r)
        logger.debug(f"批量添加去重: project={project_id}, total={len(fingerprints)}, added={added_count}")

        return [bool(result) for result in results]

    async def exists_many(self, project_id: str, fingerprints: list[str]) -> list[bool]:
        """批量检查指纹是否存在"""
        if not fingerprints:
            return []

        key = self._key(project_id)
        client = await self._get_client()
        async with client.pipeline(transaction=True) as pipeline:
            for fingerprint in fingerprints:
                pipeline.sismember(key, fingerprint)
            results = await pipeline.execute()

        exists_count = sum(1 for r in results if r)
        logger.debug(f"批量检查去重: project={project_id}, total={len(fingerprints)}, exists={exists_count}")

        return [bool(result) for result in results]

    async def size(self, project_id: str) -> int:
        """获取去重集合大小

        Requirements: 2.7
        """
        key = self._key(project_id)
        return int(await (await self._get_client()).scard(key))

    async def clear(self, project_id: str) -> bool:
        """清空去重集合"""
        key = self._key(project_id)
        result = await (await self._get_client()).delete(key)

        logger.info(f"清空去重存储: project={project_id}")

        return bool(result)

    async def get_capabilities(self, project_id: str) -> DedupStoreCapabilities:
        del project_id
        return DedupStoreCapabilities(
            storage="redis_set",
            exact=True,
            bounded=False,
            capacity_limit=None,
            retention="until_project_cleanup",
            memory_growth="linear_in_unique_fingerprints",
        )
