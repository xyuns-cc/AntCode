"""Consume worker ``LogBatch`` messages and persist them to PostgreSQL."""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import time
from collections.abc import Awaitable, Callable, Sequence
from functools import partial
from typing import Any

from antcode_contracts import data_pb2
from antcode_core.application.services.logs.log_sequence_allocator import (
    LogSequenceAllocator,
    redis_log_sequence_allocator,
)
from antcode_core.application.services.logs.postgres_log_event_query import (
    list_persisted_log_events,
)
from antcode_core.application.services.logs.postgres_log_service import (
    PostgresLogEntry,
    PostgresLogService,
    postgres_log_service,
)
from antcode_core.infrastructure.redis.control_plane import (
    log_ingest_consumer_group,
    log_ingest_stream_key,
    redis_namespace,
)
from antcode_core.infrastructure.redis.stream_client import StreamClient
from antcode_core.infrastructure.redis.stream_retention import trim_acknowledged_stream
from loguru import logger

from antcode_master.ingester.log_ingest_integrity import (
    BoundedLogBatchCodec,
    require_log_batch_integrity,
)
from antcode_master.ingester.log_ingest_message import (
    InvalidLogBatchError,
    build_log_entries,
    dead_letter_bad_frame,
    dead_letter_invalid_batch,
    require_event_ids,
)
from antcode_master.ingester.log_line_publisher import publish_persisted_log_lines

LogEventLoader = Callable[[Sequence[str]], Awaitable[list[PostgresLogEntry]]]
LogLinePublisher = Callable[[Sequence[PostgresLogEntry]], Awaitable[None]]
LogBatchIntegrityValidator = Callable[[data_pb2.LogBatch, str], Awaitable[None]]

# ACK 后裁剪节流间隔：trim_acknowledged_stream 每次要 XINFO GROUPS +
# XPENDING + XTRIM 共 3 个额外往返，逐批执行在高吞吐下开销显著；裁剪
# 只是回收空间，30s 粒度足够。
STREAM_TRIM_INTERVAL_SECONDS = 30.0


class LogIngestLoop:
    """日志摄取循环"""

    def __init__(
        self,
        stream_key: str | None = None,
        group_name: str | None = None,
        consumer_name: str | None = None,
        *,
        poll_interval: float = 1.0,
        # P2-06: block_ms 从 5000 下调到 1000 —— XREADGROUP block 期间
        # 无法响应 asyncio 取消，5s block 会让 shutdown 阻塞 5s+；多个 loop
        # gather 累积后总停机时长会非常显著。1s 是空转开销与关停响应速度的
        # 折中。
        block_ms: int = 1000,
        batch_size: int = 100,
        sequence_allocator: LogSequenceAllocator = redis_log_sequence_allocator,
        log_store: PostgresLogService = postgres_log_service,
        event_loader: LogEventLoader | None = None,
        line_publisher: LogLinePublisher | None = None,
        integrity_validator: LogBatchIntegrityValidator | None = None,
    ):
        self._stream_key = stream_key or log_ingest_stream_key()
        self._group = group_name or log_ingest_consumer_group()
        # R1-P0-2 (审查报告): consumer 名去掉 id(self)，与 result_loop 保持一致
        # P2-18: consumer 名 = "{hostname}-{pid}",多副本 master 下天然不重
        # (K8s pod 名唯一 + pid),不会出现共用 consumer 名导致 PEL owner_id
        # 反复被抢占的 starve 场景。message_id 幂等由 postgres_log_service
        # 的 event_id ON CONFLICT DO NOTHING 兜底(见 `_handle_batch` 拼
        # ``event_id=f"{msg_id}:{i}"``)。XAUTOCLAIM 只搬 PEL,DB 层去重
        # 保证同一批 Redis 消息 replay 也不会插重复行。
        self._consumer = consumer_name or f"{socket.gethostname()}-{os.getpid()}"
        self._poll_interval = poll_interval
        self._block_ms = block_ms
        self._batch_size = batch_size
        self._sequence_allocator = sequence_allocator
        self._log_store = log_store
        self._event_loader = event_loader or list_persisted_log_events
        self._line_publisher = line_publisher or publish_persisted_log_lines
        self._integrity_validator = integrity_validator or require_log_batch_integrity
        self._stream = StreamClient(codec=BoundedLogBatchCodec())
        self._running = False
        self._task: asyncio.Task | None = None
        # R1-P0-5 (审查报告): read_pending 兜底 + XAUTOCLAIM 认领。原来
        # log_ingest_loop 只 read ">" 无 pending 重试，PG 抖动一次日志静默丢失。
        self._pending_check_interval = 30.0
        self._last_pending_check = 0.0
        self._autoclaim_min_idle_ms = 60_000
        self._last_autoclaim = 0.0
        # ACK 后裁剪节流（见 STREAM_TRIM_INTERVAL_SECONDS）。-inf 保证
        # 首批 ACK 一定触发一次裁剪；_trim_backlog 记录被节流跳过的裁剪，
        # 供 stop() 时兜底补一次。
        self._last_trim = float("-inf")
        self._trim_backlog = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            "日志摄取循环已启动: stream={}, group={}, consumer={}",
            self._stream_key,
            self._group,
            self._consumer,
        )

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._trim_backlog:
            # 补上被节流跳过的裁剪，避免停机后留下已 ACK 未裁剪的积压。
            try:
                client = await self._stream._get_client()
                await trim_acknowledged_stream(client, self._stream_key, self._group)
                self._trim_backlog = False
            except Exception:
                logger.exception("停机时日志 Stream 裁剪失败")
        logger.info("日志摄取循环已停止")

    async def _run_loop(self) -> None:
        while self._running:
            try:
                messages = await self._read_messages()
                if messages:
                    await self._process_messages(messages)
                else:
                    await asyncio.sleep(self._poll_interval)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("日志摄取循环异常")
                await asyncio.sleep(self._poll_interval)

    async def _read_messages(self) -> list[Any]:
        messages = await self._read_group(read_pending=False, block_ms=self._block_ms)
        if messages:
            return messages
        if time.time() - self._last_pending_check < self._pending_check_interval:
            return []
        self._last_pending_check = time.time()
        messages = await self._read_group(read_pending=True, block_ms=1)
        if messages:
            return messages
        await self._claim_stale_pending()
        return []

    async def _read_group(self, *, read_pending: bool, block_ms: int) -> list[Any]:
        return await self._stream.xreadgroup_typed(
            stream_key=self._stream_key,
            group_name=self._group,
            consumer_name=self._consumer,
            count=self._batch_size,
            block_ms=block_ms,
            read_pending=read_pending,
        )

    async def _claim_stale_pending(self) -> None:
        try:
            claimed_total = await self._claim_pending_pages()
            if claimed_total:
                logger.warning("log_ingest_loop XAUTOCLAIM 认领旧 PEL {} 条", claimed_total)
        except Exception:
            logger.exception("log_ingest_loop XAUTOCLAIM 失败")

    async def _claim_pending_pages(self) -> int:
        next_id = "0-0"
        claimed_total = 0
        for _ in range(3):
            next_id, claimed, _ = await self._stream.xautoclaim(
                self._stream_key,
                group_name=self._group,
                consumer_name=self._consumer,
                min_idle_time_ms=self._autoclaim_min_idle_ms,
                start_id=next_id,
                count=self._batch_size,
            )
            claimed_total += len(claimed)
            if not next_id or next_id == "0-0" or len(claimed) < self._batch_size:
                break
        return claimed_total

    async def _process_messages(self, messages: list[Any]) -> None:
        ack_ids = [message.msg_id for message in messages if await self._process_message(message)]
        if ack_ids:
            await self._ack_and_trim(ack_ids)

    async def _ack_and_trim(self, ack_ids: list[str]) -> None:
        await self._stream.xack(self._stream_key, ack_ids, self._group)
        now = time.monotonic()
        if now - self._last_trim < STREAM_TRIM_INTERVAL_SECONDS:
            self._trim_backlog = True
            return
        self._last_trim = now
        self._trim_backlog = True
        try:
            client = await self._stream._get_client()
            await trim_acknowledged_stream(client, self._stream_key, self._group)
            self._trim_backlog = False
        except Exception:
            logger.exception("日志 Stream ACK 后裁剪失败")

    async def _process_message(self, message: Any) -> bool:
        if getattr(message, "decode_error", None):
            logger.warning("log_ingest: 坏 Proto 进入 DLQ: msg_id={}", message.msg_id)
            return await self._dead_letter_or_retry(message, self._dead_letter_bad_frame)
        try:
            await self._handle_batch(message.payload, msg_id=message.msg_id)
            return True
        except InvalidLogBatchError as exc:
            logger.warning("log_ingest: 非法日志批次进入 DLQ: msg_id={} err={}", message.msg_id, exc)
            return await self._dead_letter_or_retry(
                message,
                partial(self._dead_letter_invalid_batch, error=exc),
            )
        except Exception:
            logger.exception("log_ingest 处理失败，保留 PEL: msg_id={}", message.msg_id)
            return False

    @staticmethod
    async def _dead_letter_or_retry(message: Any, writer: Any) -> bool:
        try:
            await writer(message)
            return True
        except Exception:
            logger.exception("log_ingest: DLQ 写入失败，保留 PEL")
            return False

    async def _handle_batch(self, log_batch: data_pb2.LogBatch, msg_id: str = "") -> None:
        """处理单个 ``LogBatch``：批量持久化，失败抛出由上游决定 ACK。

        ``msg_id`` 是 Redis Stream 的消息 ID，用作 ``event_id`` 的稳定前缀，
        叠加 batch 内的 sequence + run_id 保证 XAUTOCLAIM 重放时幂等。
        """
        if not msg_id:
            raise ValueError("log_ingest 消息缺少 Redis message id")
        await self._integrity_validator(log_batch, msg_id)
        entries = await build_log_entries(log_batch, msg_id, self._sequence_allocator)

        try:
            await self._log_store.append_entries(entries)
        except Exception:
            logger.exception(
                "日志批量写入失败: run_id={} count={}",
                entries[0].run_id,
                len(entries),
            )
            # 让消息留在 pending，由 reclaim 重试。
            raise
        persisted = await self._event_loader(require_event_ids(entries))
        await self._line_publisher(persisted)

    async def _dead_letter_bad_frame(self, message: Any) -> None:
        client = await self._stream._get_client()
        dlq_key = f"{redis_namespace()}:dead_letter:log_ingest"
        await dead_letter_bad_frame(
            client,
            message,
            source_stream=self._stream_key,
            dlq_stream=dlq_key,
        )

    async def _dead_letter_invalid_batch(self, message: Any, error: Exception) -> None:
        client = await self._stream._get_client()
        dlq_key = f"{redis_namespace()}:dead_letter:log_ingest"
        await dead_letter_invalid_batch(
            client,
            message,
            error,
            source_stream=self._stream_key,
            dlq_stream=dlq_key,
        )


log_ingest_loop = LogIngestLoop()

__all__ = ["LogIngestLoop", "log_ingest_loop"]
