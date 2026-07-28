"""ACK-driven Spider data reporter for Gateway mode."""

from __future__ import annotations

import asyncio
import contextlib
import math
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any, Protocol

from antcode_core.spider_ingest import SpiderIngestLimits
from loguru import logger

from antcode_worker.plugins.spider.data.models import SpiderDataItem, SpiderMeta
from antcode_worker.plugins.spider.data.reporter_base import SpiderDataReporter

DEFAULT_GATEWAY_BATCH_SIZE = 30
DEFAULT_GATEWAY_FLUSH_INTERVAL_SECONDS = 5.0


class GatewaySpiderClient(Protocol):
    """Transport methods required by the Gateway reporter."""

    async def report_spider_data(
        self,
        run_id: str,
        items: list[dict[str, Any]],
    ) -> bool: ...

    async def update_spider_meta(
        self,
        run_id: str,
        meta: dict[str, Any],
    ) -> bool: ...


class GatewayDataReporter(SpiderDataReporter):
    """Buffer Spider items until Gateway confirms durable persistence."""

    def __init__(
        self,
        gateway_client: GatewaySpiderClient,
        *,
        run_id: str,
        project_id: str,
        spider_name: str,
        batch_size: int = DEFAULT_GATEWAY_BATCH_SIZE,
        flush_interval: float = DEFAULT_GATEWAY_FLUSH_INTERVAL_SECONDS,
    ) -> None:
        limits = SpiderIngestLimits.from_env()
        if batch_size <= 0:
            raise ValueError("Gateway reporter batch_size 必须大于 0")
        if batch_size > limits.max_safe_batch_items:
            raise ValueError(f"Gateway reporter batch_size 不能超过 {limits.max_safe_batch_items}")
        if not math.isfinite(flush_interval) or flush_interval <= 0:
            raise ValueError("Gateway reporter flush_interval 必须是有限正数")
        self._client = gateway_client
        self._run_id = run_id
        self._project_id = project_id
        self._spider_name = spider_name
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._buffer: list[SpiderDataItem] = []
        self._buffer_lock = asyncio.Lock()
        self._flush_task: asyncio.Task[None] | None = None
        self._running = False
        self._sequence = 0
        self._started_at: datetime | None = None

    async def start(self) -> None:
        if self._running:
            return
        self._started_at = datetime.now()
        meta = self._build_meta(status="running")
        if not await self.update_meta(meta):
            raise RuntimeError(f"Gateway Spider 初始元数据上报失败: run_id={self._run_id}")
        self._running = True
        self._flush_task = asyncio.create_task(self._periodic_flush())
        logger.info(f"Gateway Spider 数据上报器已启动: run_id={self._run_id}")

    async def stop(self) -> None:
        if self._running:
            self._running = False
            await self._cancel_periodic_flush()
        if not await self.flush():
            raise RuntimeError(f"Gateway Spider 最终刷写失败: run_id={self._run_id}")
        logger.info(f"Gateway Spider 数据上报器已停止: run_id={self._run_id}")

    async def report_item(self, item: SpiderDataItem) -> bool:
        return await self.report_batch([item])

    async def report_batch(self, items: list[SpiderDataItem]) -> bool:
        if not items:
            return True
        if not self._running:
            raise RuntimeError("Gateway Spider reporter 尚未启动")
        self._validate_items(items)
        async with self._buffer_lock:
            self._buffer.extend(self._with_sequences(items))
            should_flush = len(self._buffer) >= self._batch_size
        return await self.flush() if should_flush else True

    async def update_meta(self, meta: SpiderMeta) -> bool:
        self._validate_meta(meta)
        try:
            accepted = await self._client.update_spider_meta(
                self._run_id,
                meta.to_redis_dict(),
            )
        except Exception:
            logger.exception(f"Gateway Spider 元数据上报异常: run_id={self._run_id}")
            return False
        if not accepted:
            logger.error(f"Gateway 未确认 Spider 元数据: run_id={self._run_id}")
        return accepted

    async def finalize(
        self,
        run_id: str,
        *,
        status: str,
        items_count: int,
        pages_count: int,
        errors_count: int,
        duration_ms: float,
        errors: list[str] | None = None,
    ) -> bool:
        if not self._running:
            raise RuntimeError("Gateway Spider reporter 尚未启动")
        if run_id != self._run_id:
            raise ValueError("Gateway Spider finalize run_id 不匹配")
        if not await self.flush():
            return False
        meta = self._build_meta(
            status=status,
            items_count=items_count,
            pages_count=pages_count,
            errors_count=errors_count,
            duration_ms=duration_ms,
            errors=errors or [],
            finished_at=datetime.now(),
        )
        return await self.update_meta(meta)

    async def flush(self) -> bool:
        async with self._buffer_lock:
            if not self._buffer:
                return True
            pending = tuple(self._buffer)
            try:
                accepted = await self._client.report_spider_data(
                    self._run_id,
                    [item.to_redis_dict() for item in pending],
                )
            except Exception:
                logger.exception(f"Gateway Spider 数据上报异常: run_id={self._run_id}")
                return False
            if not accepted:
                logger.error(f"Gateway 未确认 Spider 数据: run_id={self._run_id}, count={len(pending)}")
                return False
            del self._buffer[: len(pending)]
            return True

    def _build_meta(self, *, status: str, **updates: Any) -> SpiderMeta:
        return SpiderMeta(
            run_id=self._run_id,
            project_id=self._project_id,
            spider_name=self._spider_name,
            status=status,
            started_at=self._started_at,
            **updates,
        )

    def _validate_items(self, items: Sequence[SpiderDataItem]) -> None:
        for item in items:
            if item.run_id != self._run_id or item.project_id != self._project_id:
                raise ValueError("Gateway Spider item 运行上下文不匹配")
            if item.spider_name != self._spider_name:
                raise ValueError("Gateway Spider item spider_name 不匹配")

    def _with_sequences(self, items: Sequence[SpiderDataItem]) -> list[SpiderDataItem]:
        sequenced = []
        for item in items:
            self._sequence += 1
            sequenced.append(replace(item, sequence=self._sequence))
        return sequenced

    def _validate_meta(self, meta: SpiderMeta) -> None:
        if meta.run_id != self._run_id or meta.project_id != self._project_id:
            raise ValueError("Gateway Spider meta 运行上下文不匹配")
        if meta.spider_name != self._spider_name:
            raise ValueError("Gateway Spider meta spider_name 不匹配")

    async def _cancel_periodic_flush(self) -> None:
        task = self._flush_task
        self._flush_task = None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _periodic_flush(self) -> None:
        while self._running:
            await asyncio.sleep(self._flush_interval)
            if not await self.flush():
                logger.error(f"Gateway Spider 定时刷写失败，数据保留待重试: run_id={self._run_id}")


__all__ = ["GatewayDataReporter", "GatewaySpiderClient"]
