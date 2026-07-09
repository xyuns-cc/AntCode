"""Worker log manager for transport-reported logs."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from loguru import logger

from antcode_worker.domain.enums import LogStream
from antcode_worker.domain.models import LogEntry
from antcode_worker.logs.batch import BackpressureState, BatchConfig, BatchSender
from antcode_worker.logs.realtime import RealtimeConfig, RealtimeSender
from antcode_worker.logs.streamer import LogStreamer


class TransportProtocol(Protocol):
    async def send_log(self, log: Any) -> bool: ...

    async def send_log_batch(self, logs: list[Any]) -> bool: ...

    @property
    def is_connected(self) -> bool: ...


class DropPolicy(str, Enum):
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
        self._dispatch_tasks: set[asyncio.Task] = set()
        self._dispatch_errors: list[BaseException] = []
        self._total_entries = 0
        self._total_dropped = 0
        self._stdout_lines = 0
        self._stderr_lines = 0
        # P2: 软失败累积阈值（rate-limit / not-connected 视为软跳过,不计数）
        self._soft_drop_count = 0
        self._hard_dispatch_failures = 0
        self._hard_failure_threshold = 10

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
        logger.info(f"[{self.run_id}] 日志管理器已启动")

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._streamer:
            await self._streamer.stop()
        await self._wait_dispatch_tasks()
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
        task = asyncio.create_task(self._dispatch_entry(entry))
        self._dispatch_tasks.add(task)
        task.add_done_callback(self._track_dispatch_result)

    async def _dispatch_entry(self, entry: LogEntry) -> None:
        """P2: 单路径分发——只走 batch.

        - ``False`` 不再立即升级为 RuntimeError。
        - rate-limited / disabled / not-connected / backpressure-drop 视为
          *软跳过*: ``_soft_drop_count++``，debug log，不抛异常。
        - 其它非预期失败累计到 ``_hard_dispatch_failures``，达到阈值
          才 ``raise`` 关闭 worker。
        """
        if self._should_drop(entry):
            self._drop_entry(entry)
            return

        if not self._batch:
            # batch 未启用时退回 realtime（旧行为兼容）。
            if self._realtime:
                ok = await self._realtime.write(entry)
                if not ok:
                    self._soft_drop_count += 1
                    logger.debug(
                        f"[{self.run_id}] 实时日志软跳过: seq={entry.seq}"
                    )
            return

        try:
            batch_queued = await self._batch.write(entry)
        except Exception as exc:
            self._hard_dispatch_failures += 1
            logger.error(
                f"[{self.run_id}] 批量日志入队异常 (累计 {self._hard_dispatch_failures}): {exc}"
            )
            if self._hard_dispatch_failures >= self._hard_failure_threshold:
                raise RuntimeError(
                    f"日志分发硬失败超过阈值 {self._hard_failure_threshold}: run_id={self.run_id}"
                ) from exc
            return

        if not batch_queued:
            # 已知软原因（backpressure drop / queue full / disconnected）走 debug,
            # 让 worker 在反压退潮后恢复，不撕掉整条任务。
            self._soft_drop_count += 1
            logger.debug(
                f"[{self.run_id}] 批量日志软跳过 seq={entry.seq} (backpressure 或未连接)"
            )

    async def _wait_dispatch_tasks(self) -> None:
        if not self._dispatch_tasks:
            if self._dispatch_errors:
                self._raise_dispatch_error()
            return
        tasks = list(self._dispatch_tasks)
        self._dispatch_tasks.clear()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        self._dispatch_errors.extend(result for result in results if isinstance(result, Exception))
        if self._dispatch_errors:
            self._raise_dispatch_error()

    def _track_dispatch_result(self, task: asyncio.Task) -> None:
        self._dispatch_tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error:
            self._dispatch_errors.append(error)

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

    async def archive_logs(self) -> list:
        """P6: engine._execute_task 期望在归档阶段调用此方法把 run 的日志固化。

        当前架构下日志已经通过 ``_dispatch_entry`` 流式写入 PG，PG 里就是
        权威归档，无需再上传 blob。只要在这里 flush 剩余 buffer 保证 PG
        看到全部日志即可；返回空列表让 engine 走 log_archived=False 分支
        （``ExecResult.log_archive_uri`` 保持默认）。
        """
        await self.flush()
        return []

    def get_stats(self) -> dict:
        return {
            "run_id": self.run_id,
            "running": self._running,
            "backpressure_state": self._backpressure_state.value,
            "total_entries": self._total_entries,
            "total_dropped": self._total_dropped,
            "stdout_lines": self._stdout_lines,
            "stderr_lines": self._stderr_lines,
            "soft_drop_count": self._soft_drop_count,
            "hard_dispatch_failures": self._hard_dispatch_failures,
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
