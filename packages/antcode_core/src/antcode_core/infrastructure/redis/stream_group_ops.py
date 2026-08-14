"""消费者组管理（XGROUP 系列）

所有方法的 ``group_name`` 都是必填参数：历史实现给它配了类级默认值，
两份实现的默认值还不一致（``default_workers`` / ``crawl_workers``），
导致漏传时会 ACK/读取到一个根本不存在的组。默认值已彻底移除。
"""

from loguru import logger

from antcode_core.infrastructure.redis import crawl_stream_operations as crawl_stream_ops
from antcode_core.infrastructure.redis.stream_session import StreamSession

# Redis 对不存在的 Stream 键执行 XGROUP DESTROY 时返回的错误片段（已小写化比较）
MISSING_STREAM_KEY_ERROR = "requires the key to exist"


class StreamGroupMixin(StreamSession):
    """消费者组的创建、销毁与存在性保证"""

    async def xgroup_create(
        self,
        stream_key: str,
        group_name: str,
        *,
        start_id: str = "0",
        mkstream: bool = True,
    ) -> bool:
        """创建消费者组

        Args:
            stream_key: Stream 键名
            group_name: 消费者组名称（必填）
            start_id: 起始消息 ID，"0" 从头开始，"$" 从最新开始
            mkstream: 如果 Stream 不存在是否自动创建

        Returns:
            是否创建成功（BUSYGROUP 视为已就绪）
        """
        client = await self._get_client()
        try:
            await client.xgroup_create(stream_key, group_name, id=start_id, mkstream=mkstream)
            logger.debug(f"创建消费者组成功: {stream_key} -> {group_name}")
            return True
        except Exception as e:
            # 组已存在不算错误
            if "BUSYGROUP" in str(e):
                logger.debug(f"消费者组已存在: {stream_key} -> {group_name}")
                return True
            logger.error(f"创建消费者组失败: {stream_key} -> {group_name}, 错误: {e}")
            raise

    async def xgroup_destroy(self, stream_key: str, group_name: str) -> bool:
        """删除消费者组

        语义与 ``xgroup_create`` 对齐：只有"组本来就不存在"才是正常结果，
        其余错误（Redis 不可达、权限不足……）一律 raise。历史实现把这两类
        压成同一个 ``False``，调用方无法区分"没什么可删"和"根本没删成"。
        Redis 对不存在的 Stream 键返回 ``requires the key to exist`` 错误，
        该情形等价于组不存在。

        Args:
            stream_key: Stream 键名
            group_name: 消费者组名称（必填）

        Returns:
            是否真的删除了一个已存在的消费者组
        """
        client = await self._get_client()
        try:
            result = await client.xgroup_destroy(stream_key, group_name)
        except Exception as e:
            if MISSING_STREAM_KEY_ERROR not in str(e).lower():
                logger.error(f"删除消费者组失败: {stream_key} -> {group_name}, 错误: {e}")
                raise
            logger.debug(f"Stream 不存在，消费者组无需删除: {stream_key} -> {group_name}")
            return False
        logger.debug(f"删除消费者组: {stream_key} -> {group_name}, 结果: {result}")
        return bool(result)

    async def ensure_group(self, stream_key: str, group_name: str) -> bool:
        """确保消费者组存在

        Args:
            stream_key: Stream 键名
            group_name: 消费者组名称（必填）
        """
        return await self.xgroup_create(stream_key, group_name, mkstream=True)

    async def ensure_active_group(
        self,
        stream_key: str,
        group_name: str,
        *,
        deleted_fence_key: str,
    ) -> bool:
        """仅当项目未被删除时建组（Crawl 删除 fence 由 Lua 原子校验）。"""
        client = await self._get_client()
        return await crawl_stream_ops.ensure_active_group(
            client,
            stream_key,
            group_name,
            deleted_fence_key=deleted_fence_key,
        )


__all__ = ["MISSING_STREAM_KEY_ERROR", "StreamGroupMixin"]
