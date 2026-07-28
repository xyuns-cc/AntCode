"""Gateway SpiderData gRPC sink with cancellation-safe batch recovery."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from loguru import logger

from antcode_scrapy.sinks.gateway_config import GatewaySinkConfig
from antcode_scrapy.sinks.gateway_messages import build_batch, build_item


class GatewaySpiderDataSink:
    """gRPC client-streaming sink。"""

    def __init__(
        self,
        *,
        endpoint: str,
        secure: bool = False,
        token: str = "",
        api_key: str = "",
    ) -> None:
        self._endpoint = endpoint
        self._secure = secure
        self._token = token
        self._api_key = (api_key or "").strip() or os.environ.get("ANTCODE_SPIDER_GATEWAY_API_KEY", "").strip()
        self._channel: Any = None
        self._stub: Any = None
        self._worker_id = os.environ.get("ANTCODE_WORKER_ID", "").strip() or "unknown"
        self._lease_id = os.environ.get("ANTCODE_WORKER_LEASE_ID", "").strip()
        self._run_id = ""
        self._project_id = ""
        self._spider_name = ""
        self._batch_items: list[Any] = []
        self._pending_meta: dict[str, str] = {}
        config = GatewaySinkConfig.from_env()
        self._batch_size = config.batch_size
        self._flush_interval_s = config.flush_interval_seconds
        self._limits = config.limits
        self._last_flush = 0.0
        self._lock = asyncio.Lock()
        self._flush_lock = asyncio.Lock()
        self._flush_task: asyncio.Task | None = None
        self._closing = False
        self._unreported_written = 0

    async def open(
        self,
        *,
        run_id: str,
        project_id: str,
        spider_name: str,
        namespace: str,
    ) -> None:
        import grpc  # 延迟导入
        from antcode_contracts import data_pb2_grpc

        if self._worker_id == "unknown" or not self._lease_id:
            raise RuntimeError("Gateway Spider sink 缺少 Worker ID 或当前 lease_id")
        self._run_id = run_id
        self._project_id = project_id
        self._spider_name = spider_name

        if self._secure:
            creds = grpc.ssl_channel_credentials()
            self._channel = grpc.aio.secure_channel(self._endpoint, creds)
        else:
            self._channel = grpc.aio.insecure_channel(self._endpoint)
        self._stub = data_pb2_grpc.DataServiceStub(self._channel)
        logger.info(f"GatewaySpiderDataSink 就绪: endpoint={self._endpoint} secure={self._secure} run_id={run_id}")
        self._last_flush = asyncio.get_event_loop().time()

        self._closing = False
        self._flush_task = asyncio.create_task(self._periodic_flush())
        self._flush_task.add_done_callback(self._on_flush_task_done)

    async def write_item(
        self,
        *,
        item_id: str,
        item_type: str,
        data_json: str,
        url: str,
        timestamp: str,
        sequence: int,
    ) -> tuple[bool, int]:
        """Buffer one item and flush when the count or time threshold is reached."""
        item = build_item(
            spider_name=self._spider_name,
            item_id=item_id,
            item_type=item_type,
            data_json=data_json,
            url=url,
            timestamp=timestamp,
            sequence=sequence,
        )
        if item.ByteSize() > self._limits.max_item_bytes:
            raise ValueError(f"SpiderData item 编码大小不能超过 {self._limits.max_item_bytes}")
        async with self._lock:
            self._batch_items.append(item)
            now = asyncio.get_event_loop().time()
            should_flush = (
                len(self._batch_items) >= self._batch_size or (now - self._last_flush) >= self._flush_interval_s
            )
        if should_flush:
            ok, _ = await self._flush()
            if not ok:
                return False, 0
            return True, await self.consume_written_count()
        return True, 0

    async def consume_written_count(self) -> int:
        """返回后台或前台 flush 已确认但尚未计入 Scrapy stats 的条数。"""
        async with self._lock:
            written = self._unreported_written
            self._unreported_written = 0
            return written

    async def write_meta(self, fields: dict[str, str]) -> None:
        async with self._lock:
            self._pending_meta.update(fields)

    async def _flush(self) -> tuple[bool, int]:
        """Send one buffered batch and restore it on every unconfirmed outcome."""
        async with self._flush_lock:
            pending_items, pending_meta = await self._take_pending()
            if not pending_items and not pending_meta:
                return True, 0
            batch = build_batch(
                worker_id=self._worker_id,
                run_id=self._run_id,
                project_id=self._project_id,
                lease_id=self._lease_id,
                items=pending_items,
                meta_fields=pending_meta,
            )
            if batch.ByteSize() > self._limits.max_batch_bytes:
                await self._restore_pending_safely(pending_items, pending_meta)
                raise ValueError(f"SpiderData batch 编码大小不能超过 {self._limits.max_batch_bytes}")
            return await self._send_pending(batch, pending_items, pending_meta)

    async def _take_pending(self) -> tuple[list[Any], dict[str, str]]:
        async with self._lock:
            pending_items = list(self._batch_items)
            pending_meta = dict(self._pending_meta)
            self._batch_items.clear()
            self._pending_meta.clear()
            self._last_flush = asyncio.get_event_loop().time()
            return pending_items, pending_meta

    async def _send_pending(
        self,
        batch: Any,
        pending_items: list[Any],
        pending_meta: dict[str, str],
    ) -> tuple[bool, int]:
        try:
            ack = await self._send_batch(batch)
        except asyncio.CancelledError:
            await self._restore_pending_safely(pending_items, pending_meta)
            raise
        except Exception as exc:
            logger.error(f"gateway StreamSpiderData 失败: {exc}；恢复 {len(pending_items)} 条待重试")
            await self._restore_pending_safely(pending_items, pending_meta)
            return False, 0
        accepted = int(getattr(ack, "accepted", 0))
        failed = int(getattr(ack, "failed", 0))
        if failed or accepted != len(pending_items):
            logger.warning(
                f"gateway StreamSpiderData ACK 不匹配: expected={len(pending_items)} "
                f"accepted={accepted} failed={failed}；恢复待重试"
            )
            await self._restore_pending_safely(pending_items, pending_meta)
            return False, 0
        async with self._lock:
            self._unreported_written += len(pending_items)
        return True, len(pending_items)

    async def _send_batch(self, batch: Any) -> Any:
        if self._stub is None:
            raise RuntimeError("Gateway sink 尚未 open")

        async def _iter():
            yield batch

        metadata = []
        if self._api_key:
            metadata.append(("x-api-key", self._api_key))
            if self._worker_id and self._worker_id != "unknown":
                metadata.append(("x-worker-id", self._worker_id))
        elif self._token:
            metadata.append(("authorization", f"Bearer {self._token}"))
        return await self._stub.StreamSpiderData(_iter(), metadata=metadata)

    @staticmethod
    def _on_flush_task_done(task: asyncio.Task) -> None:
        """吃掉后台 task 未处理的异常，避免 asyncio warning。"""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.warning(f"gateway 后台 flush task 异常退出: {exc}")

    async def _periodic_flush(self) -> None:
        """Flush buffered data at the configured interval."""
        while not self._closing:
            try:
                await asyncio.sleep(self._flush_interval_s)
            except asyncio.CancelledError:
                break
            if self._closing:
                break
            try:
                async with self._lock:
                    has_data = bool(self._batch_items) or bool(self._pending_meta)
                if has_data:
                    await self._flush()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # pragma: no cover
                logger.warning(f"gateway 周期 flush 失败: {exc}")

    async def _restore_pending(
        self,
        pending_items: list[Any],
        pending_meta: dict[str, str],
    ) -> None:
        """flush 失败时把 items / meta 合并回 buffer 头部。

        - items：pending 排在新进来的前面，保原始 sequence 顺序
        - meta：pending 值先落入，被期间累积的新值覆盖（新覆盖旧）
        """
        async with self._lock:
            if pending_items:
                self._batch_items = pending_items + self._batch_items
            if pending_meta:
                merged = dict(pending_meta)
                merged.update(self._pending_meta)
                self._pending_meta = merged

    async def _restore_pending_safely(
        self,
        pending_items: list[Any],
        pending_meta: dict[str, str],
    ) -> None:
        restore_task = asyncio.create_task(self._restore_pending(pending_items, pending_meta))
        try:
            await asyncio.shield(restore_task)
        except asyncio.CancelledError:
            await restore_task
            raise

    async def close(self, final_meta: dict[str, str] | None = None) -> tuple[bool, int]:
        """Stop background work, flush once, and report any remaining items."""
        self._closing = True
        if self._flush_task is not None and not self._flush_task.done():
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning(f"gateway 后台 flush 停止失败: {exc}")
        self._flush_task = None

        if final_meta:
            await self.write_meta(final_meta)
        # flush 掉最后剩余的
        flush_ok, _ = await self._flush()
        async with self._lock:
            remaining = len(self._batch_items)
        if self._channel is not None:
            try:
                await self._channel.close()
            except Exception as exc:
                logger.warning(f"gateway channel 关闭失败: {exc}")
        return flush_ok, remaining
