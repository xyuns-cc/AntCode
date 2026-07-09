"""T6-T3b: SpiderData 处理器

接收 Worker 通过 ``DataService.StreamSpiderData`` 上报的 ``SpiderDataBatch``，
把每条 ``SpiderDataItem`` **原样翻译**成 direct 模式那套 Redis Stream 字段
——字段名、类型、顺序都对齐 ``antcode_scrapy.pipelines.redis_pipeline
.AntCodeRedisPipeline.process_item`` 里 xadd 的 payload——让 web_api 侧读取
无感知 direct/gateway 两种模式的差异。

**关键约定**：
- Stream key = ``{ns}:spider:data:{run_id}``
- Meta key  = ``{ns}:spider:meta:{run_id}``（Hash）
- Index     = ``{ns}:spider:index:{project_id}``（ZSet） —— gateway 侧
  不主动写这个（run 首次 xadd 时也不知道 project 的起始时间戳），依赖
  worker 侧启动 spider 时通过 direct 或 gateway 都会走 pipeline，或者
  由 master 侧的 batch_dispatcher_service 一次性填。

**maxlen / TTL**：
- Stream maxlen 走 env ``SPIDER_STREAM_MAXLEN``（默认 10000）
- Stream / meta TTL 走 env ``SPIDER_META_TTL_SECONDS``（默认 86400）
"""

from __future__ import annotations

import os
import time

from antcode_contracts import data_pb2
from antcode_core.infrastructure.redis.control_plane import redis_namespace
from loguru import logger


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        v = int(raw)
    except ValueError:
        return default
    return v if v >= minimum else default


class SpiderDataHandler:
    """把 ``SpiderDataBatch`` 里的 items + meta 落 Redis。

    每条 item 对应一次 xadd；meta 心跳对应一次 HSET。所有 xadd 走
    ``pipeline(transaction=False)``，集群模式下按 key 自动路由（这里所有 key
    都在同一个 run_id / project_id 下，安全）。
    """

    def __init__(self, redis_client=None):
        self._redis_client = redis_client
        self._stream_maxlen = _env_int("SPIDER_STREAM_MAXLEN", 10000)
        self._meta_ttl_seconds = _env_int("SPIDER_META_TTL_SECONDS", 86400)

    async def _get_redis_client(self):
        if self._redis_client is None:
            try:
                from antcode_core.infrastructure.redis import get_redis_client

                self._redis_client = await get_redis_client()
            except ImportError:
                logger.warning("antcode_core.infrastructure.redis 不可用")
                return None
        return self._redis_client

    def _stream_key(self, run_id: str, ns: str | None = None) -> str:
        return f"{redis_namespace(ns)}:spider:data:{run_id}"

    def _meta_key(self, run_id: str, ns: str | None = None) -> str:
        return f"{redis_namespace(ns)}:spider:meta:{run_id}"

    async def handle_batch(self, batch: data_pb2.SpiderDataBatch) -> tuple[int, int]:
        """把 batch 翻译成 xadd + HSET。

        Returns:
            (accepted_count, failed_count) —— accepted 是成功入队的 item 数
        """
        redis = await self._get_redis_client()
        if redis is None:
            logger.warning("Redis 不可用，SpiderData 丢弃")
            return (0, len(batch.items))

        run_id = batch.run_id
        if not run_id:
            logger.warning(
                f"SpiderDataBatch 缺 run_id，drop worker_id={batch.worker_id}"
            )
            return (0, len(batch.items))

        stream_key = self._stream_key(run_id)
        meta_key = self._meta_key(run_id)

        # 字段名/顺序/类型必须逐字对齐 direct 模式的 AntCodeRedisPipeline
        # （否则 web_api SpiderDataItem.from_redis_dict 解析失败）。
        pipe = redis.pipeline(transaction=False)
        accepted = 0
        failed = 0
        for item in batch.items:
            payload = {
                "item_id": item.item_id,
                "run_id": run_id,
                "project_id": batch.project_id,
                "spider_name": item.spider_name,
                "item_type": item.item_type or "default",
                "data": item.data,  # bytes，直接透传
                "url": item.url,
                "timestamp": item.timestamp,
                "sequence": str(item.sequence),
            }
            try:
                pipe.xadd(
                    stream_key,
                    payload,
                    maxlen=self._stream_maxlen,
                    approximate=True,
                )
                accepted += 1
            except Exception as exc:
                logger.error(
                    f"pipe.xadd 组装失败 run_id={run_id} seq={item.sequence}: {exc}"
                )
                failed += 1

        # meta 心跳（可选） —— 与 AntCodeRedisPipeline.process_item 里每 N 条
        # HSET 一次是同一个语义
        if batch.HasField("meta"):
            meta = batch.meta
            fields: dict[str, str] = {"run_id": run_id, "project_id": batch.project_id}
            if meta.status:
                fields["status"] = meta.status
            if meta.items_count > 0:
                fields["items_count"] = str(meta.items_count)
            if meta.last_item_at:
                fields["last_item_at"] = meta.last_item_at
            if fields:
                pipe.hset(meta_key, mapping=fields)
                pipe.expire(meta_key, self._meta_ttl_seconds)

        try:
            await pipe.execute()
            # 首条时刷 stream TTL（与 direct 模式对齐）
            if accepted > 0:
                try:
                    await redis.expire(stream_key, self._meta_ttl_seconds)
                except Exception:
                    pass
        except Exception as exc:
            logger.exception(
                f"pipe.execute 失败 run_id={run_id} accepted={accepted}: {exc}"
            )
            return (0, len(batch.items))

        return (accepted, failed)


__all__ = ["SpiderDataHandler"]
