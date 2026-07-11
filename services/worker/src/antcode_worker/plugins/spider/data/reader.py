"""
爬虫数据读取器

提供从 Redis 读取爬虫数据的功能，供后续落库服务使用。
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import TYPE_CHECKING, Any, cast

from loguru import logger

from antcode_worker.plugins.spider.data.models import (
    SpiderConfig,
    SpiderDataItem,
    SpiderMeta,
)

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from antcode_worker.transport.redis.keys import RedisKeys


class SpiderDataReader:
    """
    爬虫数据读取器

    从 Redis 读取爬虫数据，支持：
    - 按 run_id 读取数据条目
    - 获取运行元数据
    - 列出项目的运行记录
    - 获取项目配置
    """

    def __init__(self, redis_client: Redis, keys: RedisKeys):
        self._redis = redis_client
        self._keys = keys

    async def read_items(
        self,
        run_id: str,
        start_id: str = "0",
        count: int = 100,
    ) -> tuple[list[SpiderDataItem], str]:
        """
        读取数据条目

        Args:
            run_id: 运行 ID
            start_id: 起始消息 ID（用于分页）
            count: 读取数量

        Returns:
            (items, last_id) 元组
        """
        try:
            stream_key = self._keys.spider_data_stream(run_id)
            results = await self._redis.xrange(
                stream_key,
                min=f"({start_id}" if start_id != "0" else "-",
                max="+",
                count=count,
            )

            items = []
            last_id = start_id

            for msg_id, data in results:
                # 解码 bytes
                decoded = {}
                for k, v in data.items():
                    key = k.decode() if isinstance(k, bytes) else k
                    val = v.decode() if isinstance(v, bytes) else v
                    decoded[key] = val

                items.append(SpiderDataItem.from_redis_dict(decoded))
                last_id = msg_id.decode() if isinstance(msg_id, bytes) else msg_id

            return items, last_id
        except Exception as e:
            logger.error(f"读取 Spider 数据失败: {e}")
            return [], start_id

    async def read_all_items(
        self,
        run_id: str,
        batch_size: int = 100,
    ) -> list[SpiderDataItem]:
        """
        读取所有数据条目

        Args:
            run_id: 运行 ID
            batch_size: 每批读取数量

        Returns:
            所有数据条目列表
        """
        all_items = []
        last_id = "0"

        while True:
            items, last_id = await self.read_items(run_id, last_id, batch_size)
            if not items:
                break
            all_items.extend(items)

        return all_items

    async def get_meta(self, run_id: str) -> SpiderMeta | None:
        """
        获取运行元数据

        Args:
            run_id: 运行 ID

        Returns:
            SpiderMeta 或 None
        """
        try:
            meta_key = self._keys.spider_meta_key(run_id)
            data = await cast(Awaitable[dict[Any, Any]], self._redis.hgetall(meta_key))

            if not data:
                return None

            # 解码 bytes
            decoded = {}
            for k, v in data.items():
                key = k.decode() if isinstance(k, bytes) else k
                val = v.decode() if isinstance(v, bytes) else v
                decoded[key] = val

            return SpiderMeta.from_redis_dict(decoded)
        except Exception as e:
            logger.error(f"获取 Spider 元数据失败: {e}")
            return None

    async def list_runs(
        self,
        project_id: str,
        start_time: float | None = None,
        end_time: float | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[str]:
        """
        列出项目的运行记录

        Args:
            project_id: 项目 ID
            start_time: 起始时间戳
            end_time: 结束时间戳
            limit: 返回数量
            offset: 偏移量

        Returns:
            run_id 列表（按时间倒序）
        """
        try:
            index_key = self._keys.spider_index_key(project_id)

            min_score = start_time if start_time is not None else "-inf"
            max_score = end_time if end_time is not None else "+inf"

            results = await self._redis.zrevrangebyscore(
                index_key,
                max=max_score,
                min=min_score,
                start=offset,
                num=limit,
            )

            return [r.decode() if isinstance(r, bytes) else r for r in results]
        except Exception as e:
            logger.error(f"列出 Spider 运行记录失败: {e}")
            return []

    async def get_config(self, project_id: str) -> SpiderConfig | None:
        """
        获取项目配置

        Args:
            project_id: 项目 ID

        Returns:
            SpiderConfig 或 None
        """
        try:
            config_key = self._keys.spider_config_key(project_id)
            data = await cast(Awaitable[dict[Any, Any]], self._redis.hgetall(config_key))

            if not data:
                return None

            # 解码 bytes
            decoded = {}
            for k, v in data.items():
                key = k.decode() if isinstance(k, bytes) else k
                val = v.decode() if isinstance(v, bytes) else v
                decoded[key] = val

            return SpiderConfig.from_redis_dict(decoded)
        except Exception as e:
            logger.error(f"获取 Spider 配置失败: {e}")
            return None

    async def set_config(self, config: SpiderConfig) -> bool:
        """
        设置项目配置

        Args:
            config: SpiderConfig 实例

        Returns:
            是否成功
        """
        try:
            config_key = self._keys.spider_config_key(config.project_id)
            await cast(
                Awaitable[Any],
                self._redis.hset(config_key, mapping=config.to_redis_dict()),
            )
            return True
        except Exception as e:
            logger.error(f"设置 Spider 配置失败: {e}")
            return False

    async def delete_run(self, run_id: str, project_id: str) -> bool:
        """
        删除运行记录

        Args:
            run_id: 运行 ID
            project_id: 项目 ID

        Returns:
            是否成功
        """
        try:
            pipe = self._redis.pipeline()

            # 删除数据流
            pipe.delete(self._keys.spider_data_stream(run_id))
            # 删除元数据
            pipe.delete(self._keys.spider_meta_key(run_id))
            # 从索引移除
            pipe.zrem(self._keys.spider_index_key(project_id), run_id)

            await pipe.execute()
            return True
        except Exception as e:
            logger.error(f"删除 Spider 运行记录失败: {e}")
            return False

    async def get_items_count(self, run_id: str) -> int:
        """
        获取数据条目数量

        Args:
            run_id: 运行 ID

        Returns:
            条目数量
        """
        try:
            stream_key = self._keys.spider_data_stream(run_id)
            return await self._redis.xlen(stream_key)
        except Exception as e:
            logger.error(f"获取 Spider 数据数量失败: {e}")
            return 0
