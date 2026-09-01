"""任务执行器抽象接口和通用功能。"""

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from loguru import logger

from antcode_worker.domain.enums import ExitReason, RunStatus
from antcode_worker.domain.models import (
    ArtifactRef,
    ExecPlan,
    ExecResult,
    LogEntry,
    RuntimeHandle,
)
from antcode_worker.executor.concurrency import ExecutionAdmission, ResizableConcurrencyGate
from antcode_worker.rule_egress_limits import RuleEgressLimits


class LogSink(Protocol):
    """日志输出接口，用于解耦执行器与日志系统。"""

    async def write(self, entry: LogEntry) -> None: ...

    async def flush(self) -> None: ...


@dataclass
class ExecutorConfig:
    max_concurrent: int = 5
    default_timeout: int = 3600  # 秒
    default_grace_period: int = 10  # SIGTERM 后等待秒数

    default_memory_limit_mb: int = 0  # 0 = 不限制
    default_cpu_limit_seconds: int = 0  # 0 = 不限制

    # 沙箱硬限制（POSIX rlimit；非 POSIX 平台自动跳过）
    enforce_rlimit: bool = True
    # 256 太低：爬虫 16 并发 + DNS + TLS + keepalive 池极易触发 EMFILE。实测
    # scrapy/playwright 子进程稳定 800~1500 fd，留余量到 2048。
    default_max_open_files: int = 2048
    default_max_processes: int = 64
    # RLIMIT_FSIZE 单文件上限，挡"失控爬虫写满磁盘"；正常项目单文件产物远不到 1GB。
    default_max_file_size_mb: int = 1024
    rule_egress_limits: RuleEgressLimits = field(default_factory=RuleEgressLimits)

    max_output_lines: int = 100000
    max_output_bytes: int = 100 * 1024 * 1024


@dataclass
class ExecutorStats:
    total_executions: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0
    timeout: int = 0
    running: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_executions": self.total_executions,
            "completed": self.completed,
            "failed": self.failed,
            "cancelled": self.cancelled,
            "timeout": self.timeout,
            "running": self.running,
        }


class BaseExecutor(ABC):
    """执行器基类：抽象 ``run``，并提供并发控制、任务注册/注销、取消与统计。"""

    def __init__(self, config: ExecutorConfig | None = None):
        self.config = config or ExecutorConfig()

        self._concurrency_gate = ResizableConcurrencyGate(self.config.max_concurrent)

        # {run_id: task_info}
        self._running_tasks: dict[str, Any] = {}
        self._lock = asyncio.Lock()

        self._stats = ExecutorStats()
        self._running = False

    @abstractmethod
    async def run(
        self,
        exec_plan: ExecPlan,
        runtime_handle: RuntimeHandle,
        log_sink: LogSink | None = None,
        *,
        admission: ExecutionAdmission | None = None,
    ) -> ExecResult:
        """执行任务：``exec_plan`` 由 Plugin 生成，``runtime_handle`` 由 RuntimeManager 提供。"""
        pass

    async def start(self) -> None:
        if self._running:
            return

        self._running = True
        logger.info(f"{self.__class__.__name__} 已启动 (并发: {self.config.max_concurrent})")

    async def resize_concurrency(self, max_concurrent: int) -> None:
        """Apply a live limit without cancelling executions already in progress."""
        await self._concurrency_gate.resize(max_concurrent)
        self.config.max_concurrent = max_concurrent

    async def stop(self, grace_period: float = 10.0) -> None:
        """停止执行器；``grace_period`` 是等待运行中任务完成的秒数。"""
        self._running = False

        run_ids = list(self._running_tasks.keys())
        for run_id in run_ids:
            await self.cancel(run_id)

        if self._running_tasks:
            logger.info(f"等待 {len(self._running_tasks)} 个任务完成...")
            await asyncio.sleep(grace_period)

        logger.info(f"{self.__class__.__name__} 已停止")

    def has_task(self, run_id: str) -> bool:
        """判断 run_id 是否仍由 executor 跟踪。"""
        return run_id in self._running_tasks

    async def cancel(self, run_id: str) -> bool:
        """取消任务。``False`` 仅表示任务不存在；真实取消失败直接抛出异常。"""
        async with self._lock:
            task_info = self._running_tasks.get(run_id)
            if not task_info:
                return False

        await self._do_cancel(run_id, task_info)
        logger.info(f"任务已取消: {run_id}")
        return True

    @abstractmethod
    async def _do_cancel(self, run_id: str, task_info: Any) -> None:
        pass

    async def _register_task(self, run_id: str, task_info: Any) -> None:
        async with self._lock:
            self._running_tasks[run_id] = task_info
            self._stats.running = len(self._running_tasks)

    async def _unregister_task(self, run_id: str) -> None:
        async with self._lock:
            self._running_tasks.pop(run_id, None)
            self._stats.running = len(self._running_tasks)

    def _update_stats(self, status: RunStatus) -> None:
        self._stats.total_executions += 1

        if status == RunStatus.SUCCESS:
            self._stats.completed += 1
        elif status in (RunStatus.FAILED, RunStatus.KILLED):
            self._stats.failed += 1
        elif status == RunStatus.CANCELLED:
            self._stats.cancelled += 1
        elif status == RunStatus.TIMEOUT:
            self._stats.timeout += 1

    def _create_result(
        self,
        run_id: str,
        status: RunStatus,
        exit_code: int | None = None,
        exit_reason: ExitReason = ExitReason.NORMAL,
        error_message: str | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        artifacts: list[ArtifactRef] | None = None,
        **kwargs: Any,
    ) -> ExecResult:
        now = datetime.now()
        started = started_at or now
        finished = finished_at or now

        duration_ms = (finished - started).total_seconds() * 1000

        return ExecResult(
            run_id=run_id,
            status=status,
            exit_code=exit_code,
            exit_reason=exit_reason,
            error_message=error_message,
            started_at=started,
            finished_at=finished,
            duration_ms=duration_ms,
            artifacts=artifacts or [],
            **kwargs,
        )

    @property
    def running_count(self) -> int:
        return len(self._running_tasks)

    @property
    def available_slots(self) -> int:
        return self.config.max_concurrent - len(self._running_tasks)

    @property
    def is_running(self) -> bool:
        return self._running

    def get_stats(self) -> dict[str, Any]:
        stats = self._stats.to_dict()
        stats["max_concurrent"] = self.config.max_concurrent
        stats["available_slots"] = self.available_slots
        return stats


class NoOpLogSink:
    """丢弃全部日志（测试或不需要日志时）。"""

    async def write(self, entry: LogEntry) -> None:
        pass

    async def flush(self) -> None:
        pass


class CallbackLogSink:
    """把日志转发给回调函数（同步或异步均可）。"""

    def __init__(
        self,
        callback: Callable[[LogEntry], None | Coroutine[Any, Any, None]],
    ):
        self._callback = callback
        self._buffer: list[LogEntry] = []

    async def write(self, entry: LogEntry) -> None:
        try:
            result = self._callback(entry)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            logger.debug(f"日志回调失败: {e}")

    async def flush(self) -> None:
        pass


class BufferedLogSink:
    """缓冲日志条目，按条数或时间间隔批量刷新。"""

    def __init__(
        self,
        flush_callback: Callable[[list[LogEntry]], None | Coroutine[Any, Any, None]],
        max_buffer_size: int = 100,
        flush_interval: float = 1.0,
    ):
        self._flush_callback = flush_callback
        self._max_buffer_size = max_buffer_size
        self._flush_interval = flush_interval
        self._buffer: list[LogEntry] = []
        self._lock = asyncio.Lock()
        self._last_flush = datetime.now()

    async def write(self, entry: LogEntry) -> None:
        async with self._lock:
            self._buffer.append(entry)

            should_flush = (
                len(self._buffer) >= self._max_buffer_size
                or (datetime.now() - self._last_flush).total_seconds() >= self._flush_interval
            )

        if should_flush:
            await self.flush()

    async def flush(self) -> None:
        async with self._lock:
            if not self._buffer:
                return

            entries = self._buffer.copy()
            self._buffer.clear()
            self._last_flush = datetime.now()

        try:
            result = self._flush_callback(entries)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            logger.error(f"日志刷新失败: {e}")
