"""
日志摄取循环

从 Redis Streams 消费 Worker 上报的 ``LogBatch`` Proto 消息并落盘。

P1a 改造：Stream 上的消息现在是 Proto bytes（``data_pb2.LogBatch``），
通过 ``StreamClient(codec=ProtoCodec(...))`` 自动解码为 typed 对象。

注意：当前 Master 还没有持久化 Redis 日志的全局循环（``task_log_service``
按需读取），本 loop 作为 P1a 的最小实现，把 LogBatch 直接转交给
``task_log_service`` 写入本地文件。后续 Agent E/F 让 worker/gateway 也写
Proto bytes 后即可联调。
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
from datetime import UTC, datetime
from typing import Any

from antcode_contracts import data_pb2
from antcode_core.application.services.logs.task_log_service import task_log_service
from antcode_core.infrastructure.redis.control_plane import log_stream_key
from antcode_core.infrastructure.redis.stream_client import ProtoCodec, StreamClient
from loguru import logger

from antcode_master.leader import ensure_leader


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
        # 默认订阅"全局聚合日志 stream"，约定 key 为 ``log_stream_key("")``
        # 形式上的占位（实际部署会在 settings 里指定专用 key）。
        self._stream_key = stream_key or log_stream_key("__aggregate__")
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
                        handled = await self._handle_batch(message.payload)
                        if handled:
                            ack_ids.append(message.msg_id)
                    except Exception as exc:
                        logger.error(f"处理日志批次失败: {exc}")

                if ack_ids:
                    await self._stream.xack(self._stream_key, ack_ids, self._group)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"日志摄取循环异常: {e}")
                await asyncio.sleep(self._poll_interval)

    async def _handle_batch(self, log_batch: data_pb2.LogBatch) -> bool:
        """处理单个 ``LogBatch``"""
        worker_id = log_batch.worker_id or ""
        for entry in log_batch.entries:
            run_id = entry.run_id
            if not run_id:
                continue
            # Proto LogType enum → 字符串
            log_type = _proto_log_type_to_str(data_pb2.LogType.Name(entry.log_type))
            content = entry.content or ""
            timestamp = _ts_to_datetime(entry.timestamp)
            sequence = entry.sequence

            await self._write_entry(
                run_id=run_id,
                worker_id=worker_id,
                log_type=log_type,
                content=content,
                timestamp=timestamp,
                sequence=sequence,
            )
        return True

    async def _write_entry(
        self,
        run_id: str,
        worker_id: str,
        log_type: str,
        content: str,
        timestamp: datetime | None,
        sequence: int,
    ) -> None:
        """把单条 LogEntry 写到本地日志文件（按 log_type 分流）"""
        try:
            paths = task_log_service.generate_log_paths(run_id, run_id)
            target = paths["error_log_path"] if log_type == "stderr" else paths["log_file_path"]
            ts_str = (timestamp or datetime.now(tz=UTC)).strftime(
                "%Y-%m-%d %H:%M:%S UTC"
            )
            line = f"[{ts_str}] [seq={sequence}] [{log_type}] {content}"
            await task_log_service.write_log(
                log_file_path=target,
                content=line,
                append=True,
                run_id=run_id,
                add_timestamp=False,
            )
        except Exception as exc:
            logger.error(
                "写日志失败 run_id={} worker_id={} type={} err={}",
                run_id, worker_id, log_type, exc,
            )


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
