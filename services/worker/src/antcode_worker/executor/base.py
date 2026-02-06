"""
执行器基类

定义任务执行的抽象接口和通用功能。

Requirements: 7.1
"""

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
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


class LogSink(Protocol):
    """
    日志接收器协议

    定义日志输出的接口，用于解耦执行器和日志系统。
    """

    async def write(self, entry: LogEntry) -> None:
        """写入日志条目"""
        ...

    async def flush(self) -> None:
        """刷新缓冲区"""
        ...


@dataclass
class ExecutorConfig:
    """执行器配置"""

    # 并发控制
    max_concurrent: int = 5

    # 默认超时（秒）
    default_timeout: int = 3600

    # 默认 grace period（SIGTERM 后等待时间）
    default_grace_period: int = 10

    # 资源限制
    default_memory_limit_mb: int = 0  # 0 = 不限制
    default_cpu_limit_seconds: int = 0  # 0 = 不限制

    # 输出限制
    max_output_lines: int = 100000
    max_output_bytes: int = 100 * 1024 * 1024  # 100MB


@dataclass
class ExecutorStats:
    """执行器统计信息"""

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
    """
    执行器基类

    定义任务执行的抽象接口：
    - run(exec_plan, runtime_handle, log_sink) -> ExecResult

    提供通用功能：
    - 并发控制（信号量）
    - 任务注册/注销
    - 取消支持
    - 统计信息

    Requirements: 7.1
    """

    def __init__(self, config: ExecutorConfig | None = None):
        """
        初始化执行器

        Args:
            config: 执行器配置
        """
        self.config = config or ExecutorConfig()

        # 并发控制
        self._semaphore = asyncio.Semaphore(self.config.max_concurrent)

        # 运行中的任务 {run_id: task_info}
        self._running_tasks: dict[str, Any] = {}
        self._lock = asyncio.Lock()

        # 统计信息
        self._stats = ExecutorStats()

        # 运行状态
        self._running = False

    @abstractmethod
    async def run(
        self,
        exec_plan: ExecPlan,
        runtime_handle: RuntimeHandle,
        log_sink: LogSink | None = None,
    ) -> ExecResult:
        """
        执行任务

        这是执行器的核心方法，子类必须实现。

        Args:
            exec_plan: 执行计划（由 Plugin 生成）
            runtime_handle: 运行时句柄（由 RuntimeManager 提供）
            log_sink: 日志接收器（可选）

        Returns:
            ExecResult 执行结果

        Requirements: 7.1
        """
        pass

    async def start(self) -> None:
        """启动执行器"""
        if self._running:
            return

        self._running = True
        logger.info(
            f"{self.__class__.__name__} 已启动 (并发: {self.config.max_concurrent})"
        )

    async def stop(self, grace_period: float = 10.0) -> None:
        """
        停止执行器

        Args:
            grace_period: 等待运行中任务完成的时间（秒）
        """
        self._running = False

        # 取消所有运行中的任务
        run_ids = list(self._running_tasks.keys())
        for run_id in run_ids:
            await self.cancel(run_id)

        # 等待任务完成
        if self._running_tasks:
            logger.info(f"等待 {len(self._running_tasks)} 个任务完成...")
            await asyncio.sleep(grace_period)

        logger.info(f"{self.__class__.__name__} 已停止")

    async def cancel(self, run_id: str) -> bool:
        """
        取消任务

        Args:
            run_id: 运行 ID

        Returns:
            是否成功取消
        """
        async with self._lock:
            task_info = self._running_tasks.get(run_id)
            if not task_info:
                return False

        try:
            await self._do_cancel(run_id, task_info)
            logger.info(f"任务已取消: {run_id}")
            return True
        except Exception as e:
            logger.error(f"取消任务失败: {run_id}, error={e}")
            return False

    @abstractmethod
    async def _do_cancel(self, run_id: str, task_info: Any) -> None:
        """
        执行取消操作（子类实现）

        Args:
            run_id: 运行 ID
            task_info: 任务信息
        """
        pass

    async def _register_task(self, run_id: str, task_info: Any) -> None:
        """注册运行中的任务"""
        async with self._lock:
            self._running_tasks[run_id] = task_info
            self._stats.running = len(self._running_tasks)

    async def _unregister_task(self, run_id: str) -> None:
        """注销任务"""
        async with self._lock:
            self._running_tasks.pop(run_id, None)
            self._stats.running = len(self._running_tasks)

    def _update_stats(self, status: RunStatus) -> None:
        """更新统计信息"""
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
        """
        创建执行结果

        Args:
            run_id: 运行 ID
            status: 运行状态
            exit_code: 退出码
            exit_reason: 退出原因
            error_message: 错误信息
            started_at: 开始时间
            finished_at: 结束时间
            artifacts: 产物列表
            **kwargs: 其他字段

        Returns:
            ExecResult
        """
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
        """运行中的任务数"""
        return len(self._running_tasks)

    @property
    def available_slots(self) -> int:
        """可用槽位数"""
        return self.config.max_concurrent - len(self._running_tasks)

    @property
    def is_running(self) -> bool:
        """是否正在运行"""
        return self._running

    def get_stats(self) -> dict[str, Any]:
        """获取统计信息"""
        stats = self._stats.to_dict()
        stats["max_concurrent"] = self.config.max_concurrent
        stats["available_slots"] = self.available_slots
        return stats


class NoOpLogSink:
    """空日志接收器（用于测试或不需要日志时）"""

    async def write(self, entry: LogEntry) -> None:
        """丢弃日志"""
        pass

    async def flush(self) -> None:
        """无操作"""
        pass


class CallbackLogSink:
    """
    回调日志接收器

    将日志转发给回调函数。
    """

    def __init__(
        self,
        callback: Callable[[LogEntry], None | Coroutine[Any, Any, None]],
    ):
        """
        初始化

        Args:
            callback: 日志回调函数（可以是同步或异步）
        """
        self._callback = callback
        self._buffer: list[LogEntry] = []

    async def write(self, entry: LogEntry) -> None:
        """写入日志"""
        try:
            result = self._callback(entry)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            logger.debug(f"日志回调失败: {e}")

    async def flush(self) -> None:
        """刷新（无操作）"""
        pass


class BufferedLogSink:
    """
    缓冲日志接收器

    缓冲日志条目，支持批量刷新。
    """

    def __init__(
        self,
        flush_callback: Callable[
            [list[LogEntry]], None | Coroutine[Any, Any, None]
        ],
        max_buffer_size: int = 100,
        flush_interval: float = 1.0,
    ):
        """
        初始化

        Args:
            flush_callback: 刷新回调函数
            max_buffer_size: 最大缓冲大小
            flush_interval: 刷新间隔（秒）
        """
        self._flush_callback = flush_callback
        self._max_buffer_size = max_buffer_size
        self._flush_interval = flush_interval
        self._buffer: list[LogEntry] = []
        self._lock = asyncio.Lock()
        self._last_flush = datetime.now()

    async def write(self, entry: LogEntry) -> None:
        """写入日志"""
        async with self._lock:
            self._buffer.append(entry)

            # 检查是否需要刷新
            should_flush = (
                len(self._buffer) >= self._max_buffer_size
                or (datetime.now() - self._last_flush).total_seconds()
                >= self._flush_interval
            )

        if should_flush:
            await self.flush()

    async def flush(self) -> None:
        """刷新缓冲区"""
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
