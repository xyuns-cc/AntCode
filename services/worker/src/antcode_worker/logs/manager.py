"""Worker log manager for transport-reported logs."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from loguru import logger

from antcode_worker.domain.enums import LogStream
from antcode_worker.domain.models import LogEntry
from antcode_worker.logs.batch import BackpressureState, BatchConfig, BatchSender
from antcode_worker.logs.realtime import RealtimeConfig, RealtimeSender
from antcode_worker.logs.streamer import LogStreamer

MAX_DISPATCH_QUEUE_SIZE = 1000


class TransportProtocol(Protocol):
    async def send_log(self, log: Any) -> bool: ...

    async def send_log_batch(self, logs: list[Any]) -> bool: ...

    @property
    def is_connected(self) -> bool: ...


class DropPolicy(StrEnum):
    NONE = "none"
    OLDEST = "oldest"
    NEWEST = "newest"
    LOW_PRIORITY = "low_priority"


@dataclass
class LogManagerConfig:
    enable_realtime: bool = True
    enable_batch: bool = True
    realtime_config: RealtimeConfig = field(default_factory=RealtimeConfig)
    batch_config: BatchConfig = field(default_factory=BatchConfig)
    drop_policy: DropPolicy = DropPolicy.LOW_PRIORITY
    priority_order: list[str] = field(default_factory=lambda: ["system", "stderr", "stdout"])


class LogManager:
    """Captures stdout/stderr and reports logs through transport."""

    def __init__(
        self,
        run_id: str,
        transport: TransportProtocol | None = None,
        config: LogManagerConfig | None = None,
        on_backpressure: Callable[[BackpressureState], None] | None = None,
        on_log_dropped: Callable[[LogEntry, str], None] | None = None,
    ):
        self.run_id = run_id
        self._transport = transport
        self._config = config or LogManagerConfig()
        self._on_backpressure = on_backpressure
        self._on_log_dropped = on_log_dropped
        self._streamer: LogStreamer | None = None
        self._realtime: RealtimeSender | None = None
        self._batch: BatchSender | None = None
        self._running = False
        self._backpressure_state = BackpressureState.NORMAL
        self._dispatch_queue: asyncio.Queue[LogEntry] = asyncio.Queue(maxsize=MAX_DISPATCH_QUEUE_SIZE)
        self._dispatch_task: asyncio.Task | None = None
        self._dispatch_errors: list[BaseException] = []
        self._total_entries = 0
        self._total_dropped = 0
        self._stdout_lines = 0
        self._stderr_lines = 0

    @property
    def backpressure_state(self) -> BackpressureState:
        return self._backpressure_state

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        await self._init_components()
        self._dispatch_task = asyncio.create_task(self._dispatch_loop())
        logger.info(f"[{self.run_id}] 日志管理器已启动")

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._streamer:
            await self._streamer.stop()
        await self._wait_dispatch_queue()
        if self._batch:
            await self._batch.flush()
            await self._batch.stop()
        if self._realtime:
            await self._realtime.stop()
        logger.info(f"[{self.run_id}] 日志管理器已停止")

    async def _init_components(self) -> None:
        # P2 改造：默认只走 batch（吞吐高 + 失败重试更优）。
        # ``enable_realtime`` 仍保留构造 RealtimeSender 用于调试 / 单元测试,
        # 但不会再被 ``_dispatch_entry`` 调用——避免 realtime+batch 同条日志
        # 双发,以及 realtime 速率限制把整条 worker 拖死。
        if self._config.enable_realtime and self._transport:
            self._realtime = RealtimeSender(
                self.run_id,
                self._transport,
                self._config.realtime_config,
                self._handle_realtime_failure,
            )
            await self._realtime.start()
        if self._config.enable_batch and self._transport:
            self._batch = BatchSender(
                self.run_id,
                self._transport,
                self._config.batch_config,
                self._handle_backpressure,
            )
            await self._batch.start()
        self._streamer = LogStreamer(self.run_id, [], self._on_log_entry)

    def _on_log_entry(self, entry: LogEntry) -> None:
        if not self._running:
            return
        self._record_entry(entry)
        try:
            self._dispatch_queue.put_nowait(entry)
        except asyncio.QueueFull as exc:
            raise RuntimeError(f"日志分发队列已满: run_id={self.run_id} limit={MAX_DISPATCH_QUEUE_SIZE}") from exc

    async def _dispatch_entry(self, entry: LogEntry) -> None:
        """Dispatch one entry and expose every transport failure."""
        if self._should_drop(entry):
            self._drop_entry(entry)
            return

        if not self._batch:
            if self._realtime is None:
                raise RuntimeError(f"日志发送器未配置: run_id={self.run_id}")
            if not await self._realtime.write(entry):
                raise RuntimeError(f"实时日志上报失败: run_id={self.run_id} seq={entry.seq}")
            return

        try:
            batch_queued = await self._batch.write(entry)
        except Exception as exc:
            raise RuntimeError(f"批量日志入队异常: run_id={self.run_id}") from exc

        if not batch_queued:
            raise RuntimeError(f"批量日志入队失败: run_id={self.run_id} seq={entry.seq}")

    async def _dispatch_loop(self) -> None:
        while True:
            entry = await self._dispatch_queue.get()
            try:
                await self._dispatch_entry(entry)
            except Exception as exc:
                self._dispatch_errors.append(exc)
            finally:
                self._dispatch_queue.task_done()

    async def _wait_dispatch_queue(self) -> None:
        await self._dispatch_queue.join()
        task = self._dispatch_task
        self._dispatch_task = None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if self._dispatch_errors:
            self._raise_dispatch_error()

    def _raise_dispatch_error(self) -> None:
        error = self._dispatch_errors[0]
        self._dispatch_errors.clear()
        raise RuntimeError(f"日志分发失败: {error}") from error

    def _should_drop(self, entry: LogEntry) -> bool:
        if self._config.drop_policy == DropPolicy.NONE:
            return False
        if self._backpressure_state not in (BackpressureState.CRITICAL, BackpressureState.BLOCKED):
            return False
        if self._config.drop_policy != DropPolicy.LOW_PRIORITY:
            return True
        stream_name = entry.stream.value
        priority = self._priority(stream_name)
        return priority >= len(self._config.priority_order) - 1

    def _priority(self, stream_name: str) -> int:
        if stream_name in self._config.priority_order:
            return self._config.priority_order.index(stream_name)
        return len(self._config.priority_order)

    def _drop_entry(self, entry: LogEntry) -> None:
        self._total_dropped += 1
        if self._on_log_dropped:
            self._on_log_dropped(entry, "backpressure")

    def _record_entry(self, entry: LogEntry) -> None:
        self._total_entries += 1
        if entry.stream == LogStream.STDOUT:
            self._stdout_lines += 1
        elif entry.stream == LogStream.STDERR:
            self._stderr_lines += 1

    def _handle_backpressure(self, state: BackpressureState) -> None:
        self._backpressure_state = state
        if self._on_backpressure:
            self._on_backpressure(state)

    def _handle_realtime_failure(self, entry: LogEntry, error: str) -> None:
        logger.error(f"[{self.run_id}] 实时日志上报失败: {error}")

    async def capture_process(self, stdout: asyncio.StreamReader, stderr: asyncio.StreamReader) -> None:
        if self._streamer:
            await self._streamer.capture_both(stdout, stderr)

    async def write(self, entry: LogEntry) -> None:
        if not self._running:
            raise RuntimeError("LogManager is not running")
        self._record_entry(entry)
        await self._dispatch_entry(entry)

    async def write_log(
        self,
        content: str,
        stream: LogStream = LogStream.STDOUT,
        level: str = "INFO",
    ) -> None:
        if self._streamer:
            await self._streamer.write_system_log(content, level)

    async def flush(self) -> None:
        if self._streamer:
            await self._streamer.flush()
        if self._batch:
            await self._batch.flush()

    def get_stats(self) -> dict:
        return {
            "run_id": self.run_id,
            "running": self._running,
            "backpressure_state": self._backpressure_state.value,
            "total_entries": self._total_entries,
            "total_dropped": self._total_dropped,
            "stdout_lines": self._stdout_lines,
            "stderr_lines": self._stderr_lines,
        }


class LogManagerFactory:
    def __init__(
        self,
        transport: TransportProtocol | None = None,
        config: LogManagerConfig | None = None,
    ):
        self._transport = transport
        self._config = config or LogManagerConfig()

    def create(self, run_id: str) -> LogManager:
        return LogManager(run_id, self._transport, self._config)
