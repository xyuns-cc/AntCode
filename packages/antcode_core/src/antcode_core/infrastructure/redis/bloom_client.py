"""Redis Bloom Filter 客户端封装

提供 Redis Bloom Filter (RedisBloom 模块) 的高级操作封装，支持：
- 过滤器创建和配置 (BF.RESERVE)
- 元素添加 (BF.ADD/BF.MADD)
- 元素检查 (BF.EXISTS/BF.MEXISTS)
- 过滤器信息查询 (BF.INFO)

注意：需要 Redis 安装 RedisBloom 模块
"""

from dataclasses import dataclass

from loguru import logger

from antcode_core.infrastructure.redis.client import get_redis_client


@dataclass
class BloomFilterInfo:
    """Bloom Filter 信息"""

    capacity: int = 0
    size: int = 0
    num_filters: int = 0
    num_items_inserted: int = 0
    expansion_rate: int = 0


class BloomFilterClient:
    """Redis Bloom Filter 客户端

    封装 RedisBloom 模块的 Bloom Filter 操作，提供：
    - 过滤器创建和管理
    - 元素添加和检查
    - 批量操作支持

    注意：
    - 需要 Redis 安装 RedisBloom 模块
    - 如果模块不可用，会降级使用 Set 实现（精确去重但内存占用更高）
    """

    # 默认配置
    DEFAULT_CAPACITY = 1000000  # 默认容量 100 万
    DEFAULT_ERROR_RATE = 0.001  # 默认误判率 0.1%

    def __init__(self, redis_client=None):
        """初始化 Bloom Filter 客户端

        Args:
            redis_client: Redis 客户端实例，为 None 时自动获取
        """
        self._redis = redis_client
        self._bloom_available = None  # 是否支持 Bloom Filter

    async def _get_client(self):
        """获取 Redis 客户端"""
        if self._redis is None:
            self._redis = await get_redis_client()
        return self._redis

    async def _check_bloom_available(self) -> bool:
        """检查 RedisBloom 模块是否可用"""
        if self._bloom_available is not None:
            return self._bloom_available

        client = await self._get_client()

        try:
            # 尝试执行 BF.INFO 命令检测模块
            await client.execute_command("BF.INFO", "__bloom_test__")
            self._bloom_available = True
        except Exception as e:
            error_str = str(e).lower()
            if "unknown command" in error_str or "err unknown" in error_str:
                logger.warning("RedisBloom 模块不可用，将使用 Set 降级实现")
                self._bloom_available = False
            elif "not found" in error_str or "does not exist" in error_str:
                # 命令存在但 key 不存在，说明模块可用
                self._bloom_available = True
            else:
                # 其他错误，假设模块可用
                self._bloom_available = True

        return self._bloom_available

    # =========================================================================
    # 过滤器管理
    # =========================================================================

    async def bf_reserve(self, key: str, capacity: int = None,
                         error_rate: float = None, expansion: int = 2,
                         nonscaling: bool = False) -> bool:
        """创建 Bloom Filter

        Args:
            key: 过滤器键名
            capacity: 预期容量
            error_rate: 误判率
            expansion: 扩展因子
            nonscaling: 是否禁止自动扩展

        Returns:
            是否创建成功
        """
        client = await self._get_client()
        capacity = capacity or self.DEFAULT_CAPACITY
        error_rate = error_rate or self.DEFAULT_ERROR_RATE

        if not await self._check_bloom_available():
            # 降级模式：使用 Set，不需要预创建
            logger.debug(f"降级模式: 跳过 BF.RESERVE for {key}")
            return True

        try:
            args = [key, error_rate, capacity]
            if expansion != 2:
                args.extend(["EXPANSION", expansion])
            if nonscaling:
                args.append("NONSCALING")

            await client.execute_command("BF.RESERVE", *args)
            logger.debug(f"创建 Bloom Filter: {key}, 容量={capacity}, 误判率={error_rate}")
            return True

        except Exception as e:
            # 已存在不算错误
            if "item exists" in str(e).lower():
                logger.debug(f"Bloom Filter 已存在: {key}")
                return True
            logger.error(f"创建 Bloom Filter 失败: {key}, 错误: {e}")
            raise

    async def ensure_filter(self, key: str, capacity: int = None,
                            error_rate: float = None) -> bool:
        """确保 Bloom Filter 存在

        Args:
            key: 过滤器键名
            capacity: 预期容量
            error_rate: 误判率

        Returns:
            是否成功
        """
        client = await self._get_client()

        # 检查是否已存在
        exists = await client.exists(key)
        if exists:
            return True

        return await self.bf_reserve(key, capacity, error_rate)

    # =========================================================================
    # 元素添加
    # =========================================================================

    async def bf_add(self, key: str, item: str) -> bool:
        """添加元素到 Bloom Filter

        Args:
            key: 过滤器键名
            item: 要添加的元素

        Returns:
            True 表示新添加，False 表示可能已存在
        """
        client = await self._get_client()

        if not await self._check_bloom_available():
            # 降级模式：使用 SADD
            result = await client.sadd(key, item)
            return bool(result)

        try:
            result = await client.execute_command("BF.ADD", key, item)
            return bool(result)
        except Exception as e:
            logger.error(f"BF.ADD 失败: {key}, 错误: {e}")
            # 降级到 Set
            result = await client.sadd(key, item)
            return bool(result)

    async def bf_madd(self, key: str, items: list) -> list:
        """批量添加元素到 Bloom Filter

        Args:
            key: 过滤器键名
            items: 要添加的元素列表

        Returns:
            结果列表，True 表示新添加，False 表示可能已存在
        """
        if not items:
            return []

        client = await self._get_client()

        if not await self._check_bloom_available():
            # 降级模式：使用 pipeline SADD
            pipe = client.pipeline()
            for item in items:
                pipe.sadd(key, item)
            results = await pipe.execute()
            return [bool(r) for r in results]

        try:
            result = await client.execute_command("BF.MADD", key, *items)
            return [bool(r) for r in result]
        except Exception as e:
            logger.error(f"BF.MADD 失败: {key}, 错误: {e}")
            # 降级到 Set
            pipe = client.pipeline()
            for item in items:
                pipe.sadd(key, item)
            results = await pipe.execute()
            return [bool(r) for r in results]

    # =========================================================================
    # 元素检查
    # =========================================================================

    async def bf_exists(self, key: str, item: str) -> bool:
        """检查元素是否存在于 Bloom Filter

        Args:
            key: 过滤器键名
            item: 要检查的元素

        Returns:
            True 表示可能存在，False 表示一定不存在
        """
        client = await self._get_client()

        if not await self._check_bloom_available():
            # 降级模式：使用 SISMEMBER
            result = await client.sismember(key, item)
            return bool(result)

        try:
            result = await client.execute_command("BF.EXISTS", key, item)
            return bool(result)
        except Exception as e:
            logger.error(f"BF.EXISTS 失败: {key}, 错误: {e}")
            # 降级到 Set
            result = await client.sismember(key, item)
            return bool(result)

    async def bf_mexists(self, key: str, items: list) -> list:
        """批量检查元素是否存在于 Bloom Filter

        Args:
            key: 过滤器键名
            items: 要检查的元素列表

        Returns:
            结果列表，True 表示可能存在，False 表示一定不存在
        """
        if not items:
            return []

        client = await self._get_client()

        if not await self._check_bloom_available():
            # 降级模式：使用 pipeline SISMEMBER
            pipe = client.pipeline()
            for item in items:
                pipe.sismember(key, item)
            results = await pipe.execute()
            return [bool(r) for r in results]

        try:
            result = await client.execute_command("BF.MEXISTS", key, *items)
            return [bool(r) for r in result]
        except Exception as e:
            logger.error(f"BF.MEXISTS 失败: {key}, 错误: {e}")
            # 降级到 Set
            pipe = client.pipeline()
            for item in items:
                pipe.sismember(key, item)
            results = await pipe.execute()
            return [bool(r) for r in results]

    # =========================================================================
    # 组合操作
    # =========================================================================

    async def add_if_not_exists(self, key: str, item: str) -> bool:
        """如果元素不存在则添加

        Args:
            key: 过滤器键名
            item: 要添加的元素

        Returns:
            True 表示成功添加（之前不存在），False 表示已存在
        """
        # BF.ADD 本身就是这个语义
        return await self.bf_add(key, item)

    async def add_batch_if_not_exists(self, key: str, items: list) -> tuple:
        """批量添加不存在的元素

        Args:
            key: 过滤器键名
            items: 要添加的元素列表

        Returns:
            (added_count, duplicate_count) 元组
        """
        if not items:
            return 0, 0

        results = await self.bf_madd(key, items)

        added = sum(1 for r in results if r)
        duplicate = len(results) - added

        return added, duplicate

    async def filter_new_items(self, key: str, items: list) -> list:
        """过滤出不存在的元素

        Args:
            key: 过滤器键名
            items: 要检查的元素列表

        Returns:
            不存在的元素列表
        """
        if not items:
            return []

        exists_results = await self.bf_mexists(key, items)

        new_items = []
        for item, exists in zip(items, exists_results, strict=False):
            if not exists:
                new_items.append(item)

        return new_items

    # =========================================================================
    # 信息查询
    # =========================================================================

    async def bf_info(self, key: str) -> BloomFilterInfo:
        """获取 Bloom Filter 信息

        Args:
            key: 过滤器键名

        Returns:
            BloomFilterInfo 对象
        """
        client = await self._get_client()

        if not await self._check_bloom_available():
            # 降级模式：返回 Set 的信息
            card = await client.scard(key)
            return BloomFilterInfo(
                capacity=0,
                size=0,
                num_filters=1,
                num_items_inserted=card,
                expansion_rate=0
            )

        try:
            result = await client.execute_command("BF.INFO", key)

            # 解析结果 [field, value, field, value, ...]
            info_dict = {}
            for i in range(0, len(result), 2):
                field = result[i]
                if isinstance(field, bytes):
                    field = field.decode("utf-8")
                value = result[i + 1]
                info_dict[field.lower()] = value

            return BloomFilterInfo(
                capacity=info_dict.get("capacity", 0),
                size=info_dict.get("size", 0),
                num_filters=info_dict.get("number of filters", 0),
                num_items_inserted=info_dict.get("number of items inserted", 0),
                expansion_rate=info_dict.get("expansion rate", 0)
            )

        except Exception as e:
            if "not found" in str(e).lower() or "does not exist" in str(e).lower():
                return BloomFilterInfo()
            logger.error(f"BF.INFO 失败: {key}, 错误: {e}")
            return BloomFilterInfo()

    async def get_item_count(self, key: str) -> int:
        """获取已添加元素数量

        Args:
            key: 过滤器键名

        Returns:
            元素数量
        """
        info = await self.bf_info(key)
        return info.num_items_inserted

    # =========================================================================
    # 清理操作
    # =========================================================================

    async def delete_filter(self, key: str) -> bool:
        """删除 Bloom Filter

        Args:
            key: 过滤器键名

        Returns:
            是否删除成功
        """
        client = await self._get_client()
        result = await client.delete(key)
        return bool(result)

    async def clear_filter(self, key: str, capacity: int = None,
                           error_rate: float = None) -> bool:
        """清空并重建 Bloom Filter

        Args:
            key: 过滤器键名
            capacity: 新容量
            error_rate: 新误判率

        Returns:
            是否成功
        """
        await self.delete_filter(key)
        return await self.bf_reserve(key, capacity, error_rate)
