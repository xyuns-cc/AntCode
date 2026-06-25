"""
日志摄取循环

从 Redis Streams 消费 Worker 上报的 ``LogBatch`` Proto 消息并落盘。

P1a 改造：Stream 上的消息是 Proto bytes（``data_pb2.LogBatch``），
通过 ``StreamClient(codec=ProtoCodec(...))`` 自动解码为 typed 对象。

P0-#2 修复：
- 原 ``_write_entry`` 逐条调用 ``task_log_service.write_log`` 产生 I/O
  风暴；失败被 ``logger.error`` 静默吞掉，消息照 ACK，pending 队列永远
  空 → reclaim 拿不到东西重试。
- 改造为一次 ``postgres_log_service.append_entries`` 批量写入；任何异常
  ``logger.exception`` + ``raise`` 让消息留在 pending，由 ``XAUTOCLAIM``
  / 下轮 ``read_pending=True`` 重试。

P1-#25 修复：
- 原默认 stream key 为 ``log_stream_key("__aggregate__")``，与 Worker
  侧实际写入路径不匹配（Worker 走 per-run-id stream）。
- 改默认订阅"全局聚合 ingest stream"：``antcode:log:ingest``。
- **P0-followup**：Worker 侧 ``redis_transport.send_log_batch`` 需要
  同步改为写到这个 ingest stream 而不是 per-run stream，否则 Master
  仍然收不到日志。本 commit 只动 Master 侧。
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
from datetime import UTC, datetime
from typing import Any

from antcode_contracts import data_pb2
from antcode_core.application.services.logs.postgres_log_service import (
    PostgresLogEntry,
    postgres_log_service,
)
from antcode_core.infrastructure.redis.control_plane import redis_namespace
from antcode_core.infrastructure.redis.stream_client import ProtoCodec, StreamClient
from loguru import logger

from antcode_master.leader import ensure_leader


def _log_ingest_stream_key(namespace: str | None = None) -> str:
    """全局日志摄取 Stream key。

    与 Worker ``log_ingest_stream_key`` helper 同名同效，本地定义以避免
    跨包修改 ``control_plane.py``（属于其他 Agent 的范围）。

    P0-followup：等 ``antcode_core.infrastructure.redis.control_plane``
    暴露同名 helper 后，把此处替换为 import 即可。
    """
    return f"{redis_namespace(namespace)}:log:ingest"


class LogIngestLoop:
    """日志摄取循环"""

    def __init__(
        self,
        stream_key: str | None = None,
        group_name: str = "antcode-log-ingest",
        consumer_name: str | None = None,
        poll_interval: float = 1.0,
        block_ms: int = 5000,
        batch_size: int = 100,
    ):
        # 默认订阅"全局聚合 ingest stream"，对齐 Worker 侧统一写入路径。
        self._stream_key = stream_key or _log_ingest_stream_key()
        self._group = group_name
        self._consumer = consumer_name or f"{socket.gethostname()}-{id(self)}"
        self._poll_interval = poll_interval
        self._block_ms = block_ms
        self._batch_size = batch_size
        self._stream = StreamClient(codec=ProtoCodec(data_pb2.LogBatch))
        self._running = False
        self._task: asyncio.Task | None = None

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
        logger.info("日志摄取循环已停止")

    async def _run_loop(self) -> None:
        while self._running:
            try:
                if not await ensure_leader():
                    await asyncio.sleep(self._poll_interval)
                    continue

                messages = await self._stream.xreadgroup_typed(
                    stream_key=self._stream_key,
                    group_name=self._group,
                    consumer_name=self._consumer,
                    count=self._batch_size,
                    block_ms=self._block_ms,
                )

                if not messages:
                    await asyncio.sleep(self._poll_interval)
                    continue

                ack_ids: list[str] = []
                for message in messages:
                    try:
                        await self._handle_batch(message.payload)
                        ack_ids.append(message.msg_id)
                    except Exception:
                        # 已在 _handle_batch 中 logger.exception；这里不再 ACK，
                        # 留在 pending 让 reclaim / 下轮 read_pending 重试。
                        pass

                if ack_ids:
                    await self._stream.xack(self._stream_key, ack_ids, self._group)

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("日志摄取循环异常")
                await asyncio.sleep(self._poll_interval)

    async def _handle_batch(self, log_batch: data_pb2.LogBatch) -> None:
        """处理单个 ``LogBatch``：批量持久化，失败抛出由上游决定 ACK。"""
        if not log_batch.entries:
            return

        worker_id = log_batch.worker_id or ""
        entries = [
            PostgresLogEntry(
                run_id=entry.run_id,
                log_type=_proto_log_type_to_str(data_pb2.LogType.Name(entry.log_type)),
                content=entry.content or "",
                timestamp=_ts_to_datetime(entry.timestamp),
                sequence=entry.sequence,
                worker_id=worker_id,
            )
            for entry in log_batch.entries
            if entry.run_id
        ]
        if not entries:
            return

        try:
            await postgres_log_service.append_entries(entries)
        except Exception:
            logger.exception(
                "日志批量写入失败: run_id={} count={}",
                entries[0].run_id,
                len(entries),
            )
            # 让消息留在 pending，由 reclaim 重试。
            raise


def _proto_log_type_to_str(name: str) -> str:
    """``LOG_TYPE_STDOUT`` → ``stdout``"""
    if name.startswith("LOG_TYPE_"):
        return name[len("LOG_TYPE_") :].lower()
    return name.lower()


def _ts_to_datetime(ts: Any) -> datetime | None:
    """``common_pb2.Timestamp`` → ``datetime``，未设置时返回 None"""
    if ts is None:
        return None
    seconds = getattr(ts, "seconds", 0)
    nanos = getattr(ts, "nanos", 0)
    if seconds == 0 and nanos == 0:
        return None
    return datetime.fromtimestamp(seconds + nanos / 1e9, tz=UTC)


log_ingest_loop = LogIngestLoop()

__all__ = ["LogIngestLoop", "log_ingest_loop"]
