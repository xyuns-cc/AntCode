"""
爬虫数据上报器

提供爬虫数据的 Redis 存储功能：
- SpiderDataReporter: 抽象接口
- RedisDataReporter: Direct 模式实现
- GatewayDataReporter: Gateway 模式实现（预留）
"""

from __future__ import annotations

import asyncio
import contextlib
from abc import ABC, abstractmethod
from datetime import datetime
from typing import TYPE_CHECKING, Any

from loguru import logger

from antcode_worker.plugins.spider.data.models import (
    SpiderDataItem,
    SpiderMeta,
)

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from antcode_worker.transport.redis.keys import RedisKeys


class SpiderDataReporter(ABC):
    """
    爬虫数据上报器抽象接口

    定义数据上报的标准接口，支持 Direct 和 Gateway 两种模式。
    """

    @abstractmethod
    async def start(self) -> None:
        """启动上报器"""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """停止上报器，刷新缓冲区"""
        ...

    @abstractmethod
    async def report_item(self, item: SpiderDataItem) -> bool:
        """上报单条数据"""
        ...

    @abstractmethod
    async def report_batch(self, items: list[SpiderDataItem]) -> bool:
        """批量上报数据"""
        ...

    @abstractmethod
    async def update_meta(self, meta: SpiderMeta) -> bool:
        """更新元数据"""
        ...

    @abstractmethod
    async def finalize(
        self,
        run_id: str,
        status: str,
        items_count: int,
        pages_count: int,
        errors_count: int,
        duration_ms: float,
        errors: list[str] | None = None,
    ) -> bool:
        """完成爬取，写入最终状态"""
        ...

    @abstractmethod
    async def flush(self) -> bool:
        """刷新缓冲区"""
        ...


class RedisDataReporter(SpiderDataReporter):
    """
    Redis 直连数据上报器（Direct 模式）

    特性：
    - 批量写入，减少网络开销
    - 自动刷新缓冲区
    - 支持 TTL 和 maxlen 配置
    """

    def __init__(
        self,
        redis_client: Redis,
        keys: RedisKeys,
        run_id: str,
        project_id: str,
        spider_name: str,
        batch_size: int = 50,
        flush_interval: float = 5.0,
        ttl_seconds: int = 86400,
        stream_max_len: int = 10000,
    ):
        self._redis = redis_client
        self._keys = keys
        self._run_id = run_id
        self._project_id = project_id
        self._spider_name = spider_name
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._ttl_seconds = ttl_seconds
        self._stream_max_len = stream_max_len

        self._buffer: list[SpiderDataItem] = []
        self._buffer_lock = asyncio.Lock()
        self._flush_task: asyncio.Task | None = None
        self._running = False
        self._sequence = 0

    async def start(self) -> None:
        """启动上报器"""
        if self._running:
            return

        self._running = True

        # 初始化元数据
        meta = SpiderMeta(
            run_id=self._run_id,
            project_id=self._project_id,
            spider_name=self._spider_name,
            status="running",
            started_at=datetime.now(),
        )
        await self.update_meta(meta)

        # 添加到项目索引
        await self._add_to_index()

        # 启动定时刷新任务
        self._flush_task = asyncio.create_task(self._periodic_flush())

        logger.info(
            f"Spider 数据上报器已启动: run_id={self._run_id}, "
            f"batch_size={self._batch_size}"
        )

    async def stop(self) -> None:
        """停止上报器"""
        if not self._running:
            return

        self._running = False

        # 取消定时刷新
        if self._flush_task:
            self._flush_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._flush_task
            self._flush_task = None

        # 刷新剩余数据
        await self.flush()

        logger.info(f"Spider 数据上报器已停止: run_id={self._run_id}")

    async def report_item(self, item: SpiderDataItem) -> bool:
        """上报单条数据"""
        async with self._buffer_lock:
            self._sequence += 1
            item.sequence = self._sequence
            self._buffer.append(item)

            if len(self._buffer) >= self._batch_size:
                return await self._flush_buffer()

        return True

    async def report_batch(self, items: list[SpiderDataItem]) -> bool:
        """批量上报数据"""
        if not items:
            return True

        async with self._buffer_lock:
            for item in items:
                self._sequence += 1
                item.sequence = self._sequence
                self._buffer.append(item)

            if len(self._buffer) >= self._batch_size:
                return await self._flush_buffer()

        return True

    async def update_meta(self, meta: SpiderMeta) -> bool:
        """更新元数据"""
        try:
            meta_key = self._keys.spider_meta_key(self._run_id)
            await self._redis.hset(meta_key, mapping=meta.to_redis_dict())

            if self._ttl_seconds > 0:
                await self._redis.expire(meta_key, self._ttl_seconds)

            return True
        except Exception as e:
            logger.error(f"更新 Spider 元数据失败: {e}")
            return False

    async def finalize(
        self,
        run_id: str,
        status: str,
        items_count: int,
        pages_count: int,
        errors_count: int,
        duration_ms: float,
        errors: list[str] | None = None,
    ) -> bool:
        """完成爬取，写入最终状态"""
        try:
            # 先刷新缓冲区
            await self.flush()

            # 更新最终元数据
            meta_key = self._keys.spider_meta_key(run_id)
            updates = {
                "status": status,
                "finished_at": datetime.now().isoformat(),
                "items_count": str(items_count),
                "pages_count": str(pages_count),
                "errors_count": str(errors_count),
                "duration_ms": str(duration_ms),
            }
            if errors:
                import json

                updates["errors"] = json.dumps(errors, ensure_ascii=False)

            await self._redis.hset(meta_key, mapping=updates)

            logger.info(
                f"Spider 爬取完成: run_id={run_id}, status={status}, "
                f"items={items_count}, pages={pages_count}, errors={errors_count}"
            )
            return True
        except Exception as e:
            logger.error(f"完成 Spider 爬取失败: {e}")
            return False

    async def flush(self) -> bool:
        """刷新缓冲区"""
        async with self._buffer_lock:
            return await self._flush_buffer()

    async def _flush_buffer(self) -> bool:
        """内部刷新方法（需在锁内调用）"""
        if not self._buffer:
            return True

        try:
            stream_key = self._keys.spider_data_stream(self._run_id)
            pipe = self._redis.pipeline()

            for item in self._buffer:
                if self._stream_max_len > 0:
                    pipe.xadd(
                        stream_key,
                        item.to_redis_dict(),
                        maxlen=self._stream_max_len,
                        approximate=True,
                    )
                else:
                    pipe.xadd(stream_key, item.to_redis_dict())

            # 设置 TTL
            if self._ttl_seconds > 0:
                pipe.expire(stream_key, self._ttl_seconds)

            await pipe.execute()

            count = len(self._buffer)
            self._buffer.clear()

            logger.debug(f"Spider 数据已刷新: run_id={self._run_id}, count={count}")
            return True
        except Exception as e:
            logger.error(f"刷新 Spider 数据失败: {e}")
            return False

    async def _periodic_flush(self) -> None:
        """定时刷新任务"""
        while self._running:
            try:
                await asyncio.sleep(self._flush_interval)
                if self._buffer:
                    await self.flush()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"定时刷新失败: {e}")

    async def _add_to_index(self) -> None:
        """添加到项目索引"""
        try:
            index_key = self._keys.spider_index_key(self._project_id)
            score = datetime.now().timestamp()
            await self._redis.zadd(index_key, {self._run_id: score})

            if self._ttl_seconds > 0:
                await self._redis.expire(index_key, self._ttl_seconds * 7)
        except Exception as e:
            logger.error(f"添加到索引失败: {e}")


class GatewayDataReporter(SpiderDataReporter):
    """
    Gateway gRPC 数据上报器（Gateway 模式）

    通过 gRPC 将数据发送到 Gateway，由 Gateway 写入 Redis。
    预留实现，待 Gateway Proto 扩展后完善。
    """

    def __init__(
        self,
        gateway_client: Any,
        run_id: str,
        project_id: str,
        spider_name: str,
        batch_size: int = 50,
    ):
        self._client = gateway_client
        self._run_id = run_id
        self._project_id = project_id
        self._spider_name = spider_name
        self._batch_size = batch_size
        self._buffer: list[SpiderDataItem] = []
        self._running = False

    async def start(self) -> None:
        self._running = True
        logger.info(f"Gateway Spider 数据上报器已启动: run_id={self._run_id}")

    async def stop(self) -> None:
        self._running = False
        await self.flush()
        logger.info(f"Gateway Spider 数据上报器已停止: run_id={self._run_id}")

    async def report_item(self, item: SpiderDataItem) -> bool:
        self._buffer.append(item)
        if len(self._buffer) >= self._batch_size:
            return await self.flush()
        return True

    async def report_batch(self, items: list[SpiderDataItem]) -> bool:
        self._buffer.extend(items)
        if len(self._buffer) >= self._batch_size:
            return await self.flush()
        return True

    async def update_meta(self, meta: SpiderMeta) -> bool:
        # TODO: 通过 gRPC 调用 Gateway 更新元数据
        logger.warning("Gateway 模式 update_meta 尚未实现")
        return True

    async def finalize(
        self,
        run_id: str,
        status: str,
        items_count: int,
        pages_count: int,
        errors_count: int,
        duration_ms: float,
        errors: list[str] | None = None,
    ) -> bool:
        await self.flush()
        # TODO: 通过 gRPC 调用 Gateway 完成爬取
        logger.warning("Gateway 模式 finalize 尚未实现")
        return True

    async def flush(self) -> bool:
        if not self._buffer:
            return True
        # TODO: 通过 gRPC 批量发送数据到 Gateway
        logger.warning("Gateway 模式 flush 尚未实现")
        self._buffer.clear()
        return True


async def create_data_reporter(
    mode: str,
    run_id: str,
    project_id: str,
    spider_name: str,
    redis_client: Redis | None = None,
    keys: RedisKeys | None = None,
    gateway_client: Any = None,
    **kwargs: Any,
) -> SpiderDataReporter:
    """
    创建数据上报器工厂方法

    Args:
        mode: 模式 ("direct" 或 "gateway")
        run_id: 运行 ID
        project_id: 项目 ID
        spider_name: 爬虫名称
        redis_client: Redis 客户端（Direct 模式）
        keys: Redis Key 生成器（Direct 模式）
        gateway_client: Gateway gRPC 客户端（Gateway 模式）
        **kwargs: 其他配置参数

    Returns:
        SpiderDataReporter 实例
    """
    if mode == "direct":
        if not redis_client or not keys:
            raise ValueError("Direct 模式需要 redis_client 和 keys")

        reporter = RedisDataReporter(
            redis_client=redis_client,
            keys=keys,
            run_id=run_id,
            project_id=project_id,
            spider_name=spider_name,
            batch_size=kwargs.get("batch_size", 50),
            flush_interval=kwargs.get("flush_interval", 5.0),
            ttl_seconds=kwargs.get("ttl_seconds", 86400),
            stream_max_len=kwargs.get("stream_max_len", 10000),
        )
    elif mode == "gateway":
        if not gateway_client:
            raise ValueError("Gateway 模式需要 gateway_client")

        reporter = GatewayDataReporter(
            gateway_client=gateway_client,
            run_id=run_id,
            project_id=project_id,
            spider_name=spider_name,
            batch_size=kwargs.get("batch_size", 50),
        )
    else:
        raise ValueError(f"不支持的模式: {mode}")

    await reporter.start()
    return reporter
