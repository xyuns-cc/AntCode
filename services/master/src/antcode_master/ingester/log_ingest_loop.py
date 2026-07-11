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
import os
import socket
import time
from datetime import UTC, datetime
from typing import Any

from antcode_contracts import data_pb2
from antcode_core.application.services.logs.postgres_log_service import (
    PostgresLogEntry,
    postgres_log_service,
)
from antcode_core.infrastructure.redis.control_plane import redis_namespace
from antcode_core.infrastructure.redis.stream_client import ProtoCodec, StreamClient
from antcode_core.infrastructure.redis.stream_retention import trim_acknowledged_stream
from loguru import logger


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
        group_name: str | None = None,
        consumer_name: str | None = None,
        poll_interval: float = 1.0,
        # P2-06: block_ms 从 5000 下调到 1000 —— XREADGROUP block 期间
        # 无法响应 asyncio 取消，5s block 会让 shutdown 阻塞 5s+；多个 loop
        # gather 累积后总停机时长会非常显著。1s 是空转开销与关停响应速度的
        # 折中。
        block_ms: int = 1000,
        batch_size: int = 100,
    ):
        # 默认订阅"全局聚合 ingest stream"，对齐 Worker 侧统一写入路径。
        # E5: 默认 group 带 namespace，与其它 loop 保持一致
        from antcode_core.infrastructure.redis.control_plane import (
            log_ingest_consumer_group,
        )

        self._stream_key = stream_key or _log_ingest_stream_key()
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
        self._stream = StreamClient(codec=ProtoCodec(data_pb2.LogBatch))
        self._running = False
        self._task: asyncio.Task | None = None
        # R1-P0-5 (审查报告): read_pending 兜底 + XAUTOCLAIM 认领。原来
        # log_ingest_loop 只 read ">" 无 pending 重试，PG 抖动一次日志静默丢失。
        self._pending_check_interval = 30.0
        self._last_pending_check = 0.0
        self._autoclaim_min_idle_ms = 60_000
        self._last_autoclaim = 0.0

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
                # T6-T2: 去掉 leader gate —— log_ingest 走 consumer group，
                # 天然按 consumer name 分区，多 master 实例可分担负载。
                messages = await self._stream.xreadgroup_typed(
                    stream_key=self._stream_key,
                    group_name=self._group,
                    consumer_name=self._consumer,
                    count=self._batch_size,
                    block_ms=self._block_ms,
                )

                if not messages:
                    # R1-P0-5 (审查报告): 无新消息时读自己的 PEL 兜底 —— PG
                    # 抖动一次日志批次会留在 pending，之前主循环无 read_pending
                    # 分支，本进程存活期内该批日志永不落库。
                    now = time.time()
                    if now - self._last_pending_check >= self._pending_check_interval:
                        self._last_pending_check = now
                        messages = await self._stream.xreadgroup_typed(
                            stream_key=self._stream_key,
                            group_name=self._group,
                            consumer_name=self._consumer,
                            count=self._batch_size,
                            block_ms=1,
                            read_pending=True,
                        )
                        # 认领旧 consumer 遗留 PEL（R1-P0-2 一并覆盖）
                        if not messages:
                            try:
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
                                if claimed_total:
                                    logger.warning(
                                        "log_ingest_loop XAUTOCLAIM 认领旧 PEL {} 条",
                                        claimed_total,
                                    )
                            except Exception as exc:
                                logger.debug("XAUTOCLAIM 失败: {}", exc)
                    if not messages:
                        await asyncio.sleep(self._poll_interval)
                        continue

                ack_ids: list[str] = []
                for message in messages:
                    try:
                        await self._handle_batch(message.payload, msg_id=message.msg_id)
                        ack_ids.append(message.msg_id)
                    except Exception:
                        # 已在 _handle_batch 中 logger.exception；这里不再 ACK，
                        # 留在 pending 让 reclaim / 下轮 read_pending 重试。
                        pass

                if ack_ids:
                    await self._stream.xack(self._stream_key, ack_ids, self._group)
                    try:
                        client = await self._stream._get_client()
                        await trim_acknowledged_stream(
                            client,
                            self._stream_key,
                            self._group,
                        )
                    except Exception:
                        logger.exception("日志 Stream ACK 后裁剪失败")

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("日志摄取循环异常")
                await asyncio.sleep(self._poll_interval)

    async def _handle_batch(self, log_batch: data_pb2.LogBatch, msg_id: str = "") -> None:
        """处理单个 ``LogBatch``：批量持久化，失败抛出由上游决定 ACK。

        ``msg_id`` 是 Redis Stream 的消息 ID，用作 ``event_id`` 的稳定前缀，
        叠加 batch 内的 sequence + run_id 保证 XAUTOCLAIM 重放时幂等。
        """
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
                event_id=(f"{msg_id}:{i}" if msg_id else None),
            )
            for i, entry in enumerate(log_batch.entries)
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
