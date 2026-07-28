"""T6-T3b: SpiderData 处理器

接收 Worker 通过 ``DataService.StreamSpiderData`` 上报的 ``SpiderDataBatch``，
把每条 ``SpiderDataItem`` **原样翻译**成 direct 模式那套 Redis Stream 字段
——字段名、类型、顺序都对齐 ``antcode_scrapy.pipelines.redis_pipeline
.AntCodeRedisPipeline.process_item`` 里 xadd 的 payload——让 web_api 侧读取
无感知 direct/gateway 两种模式的差异。

**关键约定**：
- Stream key = ``{{ns}}:spider:<run_id>:data``
- Meta key  = ``{{ns}}:spider:<run_id>:meta``（Hash）
- Activity index = ``{{ns}}:spider:index:<project_id>``（ZSet）
- Expiry index   = ``{{ns}}:spider:index:expiry:<project_id>``（ZSet）
  在 ``status=running`` 的首个 meta batch 中写入。

**maxlen / TTL**：
- Stream maxlen 走 env ``SPIDER_STREAM_MAXLEN``（默认 0，不裁剪）
- Stream / meta / marker TTL 和 index member 过期时间走 env ``SPIDER_META_TTL_SECONDS``
  （默认 0，不自动过期）
"""

from __future__ import annotations

from typing import Any, Protocol

from antcode_contracts import data_pb2
from antcode_core.infrastructure.redis.control_plane import redis_namespace
from antcode_core.infrastructure.redis.keys import RedisKeys
from antcode_core.spider_ingest import SpiderIngestLimits, validate_spider_json
from antcode_core.spider_retention import SpiderRetention
from antcode_core.spider_write_fence import SpiderWriteIdentity
from loguru import logger

from antcode_gateway.handlers.spider_item_writer import IdempotentSpiderItemWriter, SpiderItemWriteResult
from antcode_gateway.handlers.spider_meta_writer import SpiderMetaWriter

_META_STRING_FIELDS = (
    "status",
    "last_item_at",
    "spider_name",
    "started_at",
    "finished_at",
    "config",
    "errors",
)
_META_NUMBER_FIELDS = ("items_count", "pages_count", "errors_count", "duration_ms")
MAX_ITEM_ID_LENGTH = 128
MAX_RUN_ID_LENGTH = 64
MAX_PROJECT_ID_LENGTH = 64


class SpiderItemWriter(Protocol):
    async def write(
        self,
        stream_key: str,
        marker_key: str,
        marker_order_key: str,
        *,
        identity: SpiderWriteIdentity,
        tombstone_key: str,
        index_key: str,
        index_expiry_key: str,
        payloads: list[dict[str, Any]],
    ) -> SpiderItemWriteResult: ...


class SpiderMetaWrite(Protocol):
    async def write(
        self,
        meta_key: str,
        tombstone_key: str,
        *,
        identity: SpiderWriteIdentity,
        marker_key: str,
        index_key: str,
        index_expiry_key: str,
        fields: dict[str, Any],
    ) -> None: ...


class SpiderDataHandler:
    """把 ``SpiderDataBatch`` 里的 items + meta 落 Redis。

    items 和 meta 均通过同槽 Lua 校验 Lease/ownership/tombstone 后原子写入，
    item digest marker 提供跨重试幂等性。
    """

    def __init__(
        self,
        redis_client=None,
        *,
        item_writer: SpiderItemWriter | None = None,
        meta_writer: SpiderMetaWrite | None = None,
    ):
        self._redis_client = redis_client
        self._retention = SpiderRetention.from_env(
            stream_max_len_env="SPIDER_STREAM_MAXLEN",
            ttl_seconds_env="SPIDER_META_TTL_SECONDS",
        )
        self._item_writer = item_writer
        self._meta_writer = meta_writer
        self._limits = SpiderIngestLimits.from_env()

    async def _get_redis_client(self):
        if self._redis_client is None:
            try:
                from antcode_core.infrastructure.redis import get_redis_client

                self._redis_client = await get_redis_client()
            except ImportError as exc:
                raise RuntimeError("antcode_core.infrastructure.redis 不可用") from exc
        return self._redis_client

    def _stream_key(self, run_id: str, ns: str | None = None) -> str:
        return RedisKeys(namespace=redis_namespace(ns)).spider_data_stream(run_id)

    def _meta_key(self, run_id: str, ns: str | None = None) -> str:
        return RedisKeys(namespace=redis_namespace(ns)).spider_meta_key(run_id)

    def _item_ids_key(self, run_id: str, ns: str | None = None) -> str:
        return RedisKeys(namespace=redis_namespace(ns)).spider_item_ids_key(run_id)

    def _item_order_key(self, run_id: str, ns: str | None = None) -> str:
        return RedisKeys(namespace=redis_namespace(ns)).spider_item_order_key(run_id)

    def _tombstone_key(self, run_id: str, ns: str | None = None) -> str:
        return RedisKeys(namespace=redis_namespace(ns)).spider_tombstone_key(run_id)

    def _index_key(self, project_id: str, ns: str | None = None) -> str:
        return RedisKeys(namespace=redis_namespace(ns)).spider_index_key(project_id)

    def _index_expiry_key(self, project_id: str, ns: str | None = None) -> str:
        return RedisKeys(namespace=redis_namespace(ns)).spider_index_expiry_key(project_id)

    async def handle_batch(self, batch: data_pb2.SpiderDataBatch) -> tuple[int, int]:
        """把 batch 翻译成 xadd + HSET。

        Returns:
            (accepted_count, failed_count) —— accepted 是成功入队的 item 数
        """
        redis = await self._get_redis_client()
        if redis is None:
            raise RuntimeError("Redis 不可用，SpiderData 无法持久化")

        run_id = self._canonical_id("run_id", batch.run_id, MAX_RUN_ID_LENGTH)
        self._canonical_id("project_id", batch.project_id, MAX_PROJECT_ID_LENGTH)
        self._validate_batch_size(batch)

        stream_key = self._stream_key(run_id)
        meta_key = self._meta_key(run_id)
        payloads, failed = self._build_item_payloads(batch)
        identity = SpiderWriteIdentity(
            namespace=redis_namespace(),
            worker_id=batch.worker_id,
            lease_id=batch.lease_id,
            run_id=run_id,
            project_id=batch.project_id,
        )
        accepted = await self._write_items(
            redis,
            identity=identity,
            stream_key=stream_key,
            payloads=payloads,
        )
        await self._write_meta(redis, identity=identity, meta_key=meta_key, batch=batch)
        return (accepted, failed)

    def _build_item_payloads(self, batch: data_pb2.SpiderDataBatch) -> tuple[list[dict], int]:
        payloads: list[dict] = []
        failed = 0
        for item in batch.items:
            try:
                payloads.append(self._item_payload(batch, item))
            except Exception as exc:
                logger.error(f"SpiderData item 无效 run_id={batch.run_id} seq={item.sequence}: {exc}")
                failed += 1
        return payloads, failed

    async def _write_items(
        self,
        redis,
        *,
        identity: SpiderWriteIdentity,
        stream_key: str,
        payloads: list[dict],
    ) -> int:
        if not payloads:
            return 0
        writer = self._get_item_writer(redis)
        result = await writer.write(
            stream_key,
            self._item_ids_key(identity.run_id),
            self._item_order_key(identity.run_id),
            identity=identity,
            tombstone_key=self._tombstone_key(identity.run_id),
            index_key=self._index_key(identity.project_id),
            index_expiry_key=self._index_expiry_key(identity.project_id),
            payloads=payloads,
        )
        if result.duplicates:
            logger.info(
                "SpiderData 幂等重放: stream={} accepted={} duplicates={}",
                stream_key,
                result.accepted,
                result.duplicates,
            )
        return result.accepted

    def _get_item_writer(self, redis) -> SpiderItemWriter:
        if self._item_writer is None:
            self._item_writer = IdempotentSpiderItemWriter(
                redis,
                stream_max_len=self._retention.stream_max_len,
                ttl_seconds=self._retention.ttl_seconds,
            )
        return self._item_writer

    async def _write_meta(
        self,
        redis,
        *,
        identity: SpiderWriteIdentity,
        meta_key: str,
        batch: data_pb2.SpiderDataBatch,
    ) -> None:
        fields = self._meta_fields(batch)
        if fields is None:
            return
        if self._meta_writer is None:
            self._meta_writer = SpiderMetaWriter(redis, ttl_seconds=self._retention.ttl_seconds)
        await self._meta_writer.write(
            meta_key,
            self._tombstone_key(identity.run_id),
            identity=identity,
            marker_key=self._item_ids_key(identity.run_id),
            index_key=self._index_key(identity.project_id),
            index_expiry_key=self._index_expiry_key(identity.project_id),
            fields=fields,
        )

    def _validate_batch_size(self, batch: data_pb2.SpiderDataBatch) -> None:
        if len(batch.items) > self._limits.max_batch_items:
            raise ValueError(f"SpiderData batch items 超限: {len(batch.items)} > {self._limits.max_batch_items}")
        encoded_bytes = batch.ByteSize()
        if encoded_bytes > self._limits.max_batch_bytes:
            raise ValueError(f"SpiderData batch bytes 超限: {encoded_bytes} > {self._limits.max_batch_bytes}")

    def _item_payload(self, batch: data_pb2.SpiderDataBatch, item) -> dict:
        item_id = item.item_id.strip()
        if not item_id:
            raise ValueError("item_id 不能为空")
        if len(item_id) > MAX_ITEM_ID_LENGTH:
            raise ValueError(f"item_id 长度不能超过 {MAX_ITEM_ID_LENGTH}")
        if item.ByteSize() > self._limits.max_item_bytes:
            raise ValueError(f"item 编码大小不能超过 {self._limits.max_item_bytes}")
        validate_spider_json(item.data, self._limits.max_item_bytes)
        for name in ("spider_name", "item_type", "url", "timestamp"):
            if len(getattr(item, name)) > self._limits.max_text_length:
                raise ValueError(f"{name} 长度不能超过 {self._limits.max_text_length}")
        if item.sequence <= 0:
            raise ValueError("sequence 必须为正整数")
        return {
            "item_id": item_id,
            "run_id": batch.run_id,
            "project_id": batch.project_id,
            "spider_name": item.spider_name,
            "item_type": item.item_type or "default",
            "data": item.data,
            "url": item.url,
            "timestamp": item.timestamp,
            "sequence": str(item.sequence),
        }

    @staticmethod
    def _canonical_id(name: str, value: str, max_length: int) -> str:
        if not value or value != value.strip():
            raise ValueError(f"{name} 必须是无首尾空白的非空标识")
        if len(value) > max_length:
            raise ValueError(f"{name} 长度不能超过 {max_length}")
        return value

    @staticmethod
    def _meta_fields(batch: data_pb2.SpiderDataBatch) -> dict[str, Any] | None:
        if not batch.HasField("meta"):
            return None
        meta = batch.meta
        fields: dict[str, Any] = {"run_id": batch.run_id, "project_id": batch.project_id}
        for name in _META_STRING_FIELDS:
            value = getattr(meta, name)
            if value:
                fields[name] = value
        for name in _META_NUMBER_FIELDS:
            if meta.HasField(name):
                fields[name] = str(getattr(meta, name))
        return fields


__all__ = ["SpiderDataHandler"]
