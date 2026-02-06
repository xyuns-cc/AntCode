"""
资源限制模块

实现 timeout 和 concurrency 限制，以及 CPU/memory limits（best-effort）。

Requirements: 7.3
"""

import asyncio
import contextlib
import signal
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from loguru import logger

try:
    import psutil

    HAS_PSUTIL = True
except ImportError:
    psutil = None
    HAS_PSUTIL = False


@dataclass
class ResourceLimits:
    """
    资源限制配置

    Requirements: 7.3
    """

    # 超时限制（秒）
    timeout_seconds: int = 3600

    # Grace period（SIGTERM 后等待时间，秒）
    grace_period_seconds: int = 10

    # 内存限制（MB，0 = 不限制）
    memory_limit_mb: int = 0

    # CPU 时间限制（秒，0 = 不限制）
    cpu_limit_seconds: int = 0

    # 输出限制
    max_output_lines: int = 100000
    max_output_bytes: int = 100 * 1024 * 1024  # 100MB


@dataclass
class ResourceUsage:
    """资源使用情况"""

    # CPU 使用
    cpu_time_seconds: float = 0
    cpu_percent: float = 0

    # 内存使用
    memory_rss_mb: float = 0
    memory_vms_mb: float = 0
    memory_peak_mb: float = 0

    # IO 使用
    io_read_bytes: int = 0
    io_write_bytes: int = 0

    # 时间
    wall_time_seconds: float = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "cpu_time_seconds": self.cpu_time_seconds,
            "cpu_percent": self.cpu_percent,
            "memory_rss_mb": self.memory_rss_mb,
            "memory_vms_mb": self.memory_vms_mb,
            "memory_peak_mb": self.memory_peak_mb,
            "io_read_bytes": self.io_read_bytes,
            "io_write_bytes": self.io_write_bytes,
            "wall_time_seconds": self.wall_time_seconds,
        }


class ConcurrencyLimiter:
    """
    并发限制器

    使用信号量控制并发执行数量。

    Requirements: 7.3
    """

    def __init__(self, max_concurrent: int = 5):
        """
        初始化并发限制器

        Args:
            max_concurrent: 最大并发数
        """
        self._max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._current = 0
        self._lock = asyncio.Lock()

        # 统计
        self._total_acquired = 0
        self._total_rejected = 0
        self._total_wait_time_ms = 0

    @property
    def max_concurrent(self) -> int:
        """最大并发数"""
        return self._max_concurrent

    @property
    def current(self) -> int:
        """当前并发数"""
        return self._current

    @property
    def available(self) -> int:
        """可用槽位数"""
        return self._max_concurrent - self._current

    async def acquire(self, timeout: float | None = None) -> bool:
        """
        获取执行槽位

        Args:
            timeout: 超时时间（秒），None 表示无限等待

        Returns:
            是否成功获取
        """
        start_time = datetime.now()

        try:
            if timeout is not None:
                acquired = await asyncio.wait_for(
                    self._semaphore.acquire(), timeout=timeout
                )
            else:
                acquired = await self._semaphore.acquire()

            if acquired:
                async with self._lock:
                    self._current += 1
                    self._total_acquired += 1

                wait_time = (datetime.now() - start_time).total_seconds() * 1000
                self._total_wait_time_ms += wait_time

                return True

            return False

        except TimeoutError:
            async with self._lock:
                self._total_rejected += 1
            return False

    def release(self) -> None:
        """释放执行槽位"""
        self._semaphore.release()
        asyncio.create_task(self._decrement_current())

    async def _decrement_current(self) -> None:
        """减少当前计数"""
        async with self._lock:
            self._current = max(0, self._current - 1)

    @contextlib.asynccontextmanager
    async def limit(self, timeout: float | None = None):
        """
        并发限制上下文管理器

        Args:
            timeout: 超时时间（秒）

        Yields:
            是否成功获取槽位

        Example:
            async with limiter.limit() as acquired:
                if acquired:
                    await do_work()
        """
        acquired = await self.acquire(timeout)
        try:
            yield acquired
        finally:
            if acquired:
                self.release()

    def get_stats(self) -> dict[str, Any]:
        """获取统计信息"""
        return {
            "max_concurrent": self._max_concurrent,
            "current": self._current,
            "available": self.available,
            "total_acquired": self._total_acquired,
            "total_rejected": self._total_rejected,
            "avg_wait_time_ms": (
                self._total_wait_time_ms / self._total_acquired
                if self._total_acquired > 0
                else 0
            ),
        }


class TimeoutManager:
    """
    超时管理器

    管理任务执行超时。

    Requirements: 7.3
    """

    def __init__(self, default_timeout: int = 3600, default_grace_period: int = 10):
        """
        初始化超时管理器

        Args:
            default_timeout: 默认超时时间（秒）
            default_grace_period: 默认 grace period（秒）
        """
        self._default_timeout = default_timeout
        self._default_grace_period = default_grace_period

        # 活跃的超时任务
        self._timeouts: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    async def start_timeout(
        self,
        task_id: str,
        timeout: int | None = None,
        on_timeout: Any = None,
    ) -> None:
        """
        启动超时计时器

        Args:
            task_id: 任务 ID
            timeout: 超时时间（秒）
            on_timeout: 超时回调
        """
        timeout_seconds = timeout or self._default_timeout

        async def timeout_handler():
            await asyncio.sleep(timeout_seconds)
            logger.warning(f"任务超时: {task_id} ({timeout_seconds}s)")
            if on_timeout:
                if asyncio.iscoroutinefunction(on_timeout):
                    await on_timeout(task_id)
                else:
                    on_timeout(task_id)

        async with self._lock:
            # 取消已存在的超时
            if task_id in self._timeouts:
                self._timeouts[task_id].cancel()

            # 创建新的超时任务
            self._timeouts[task_id] = asyncio.create_task(timeout_handler())

    async def cancel_timeout(self, task_id: str) -> None:
        """
        取消超时计时器

        Args:
            task_id: 任务 ID
        """
        async with self._lock:
            if task_id in self._timeouts:
                self._timeouts[task_id].cancel()
                del self._timeouts[task_id]

    async def reset_timeout(
        self,
        task_id: str,
        timeout: int | None = None,
        on_timeout: Any = None,
    ) -> None:
        """
        重置超时计时器

        Args:
            task_id: 任务 ID
            timeout: 新的超时时间（秒）
            on_timeout: 超时回调
        """
        await self.cancel_timeout(task_id)
        await self.start_timeout(task_id, timeout, on_timeout)

    @contextlib.asynccontextmanager
    async def timeout_context(
        self,
        task_id: str,
        timeout: int | None = None,
        on_timeout: Any = None,
    ):
        """
        超时上下文管理器

        Args:
            task_id: 任务 ID
            timeout: 超时时间（秒）
            on_timeout: 超时回调

        Example:
            async with timeout_manager.timeout_context("task-1", 60):
                await do_work()
        """
        await self.start_timeout(task_id, timeout, on_timeout)
        try:
            yield
        finally:
            await self.cancel_timeout(task_id)


class ResourceMonitor:
    """
    资源监控器

    监控进程的 CPU 和内存使用。

    Requirements: 7.3
    """

    def __init__(self, check_interval: float = 1.0):
        """
        初始化资源监控器

        Args:
            check_interval: 检查间隔（秒）
        """
        self._check_interval = check_interval
        self._monitors: dict[str, asyncio.Task] = {}
        self._usage: dict[str, ResourceUsage] = {}
        self._lock = asyncio.Lock()

    async def start_monitoring(
        self,
        task_id: str,
        pid: int,
        limits: ResourceLimits | None = None,
        on_limit_exceeded: Any = None,
    ) -> None:
        """
        开始监控进程

        Args:
            task_id: 任务 ID
            pid: 进程 ID
            limits: 资源限制
            on_limit_exceeded: 超限回调
        """
        if not HAS_PSUTIL:
            logger.debug("psutil 不可用，跳过资源监控")
            return

        async def monitor():
            usage = ResourceUsage()
            self._usage[task_id] = usage
            start_time = datetime.now()

            try:
                p = psutil.Process(pid)
            except (psutil.NoSuchProcess, ProcessLookupError):
                return

            try:
                while True:
                    try:
                        # 更新 CPU 时间
                        cpu_times = p.cpu_times()
                        usage.cpu_time_seconds = cpu_times.user + cpu_times.system
                        usage.cpu_percent = p.cpu_percent()

                        # 更新内存使用
                        memory_info = p.memory_info()
                        usage.memory_rss_mb = memory_info.rss / 1024 / 1024
                        usage.memory_vms_mb = memory_info.vms / 1024 / 1024
                        usage.memory_peak_mb = max(
                            usage.memory_peak_mb, usage.memory_rss_mb
                        )

                        # 更新 IO 使用
                        try:
                            io_counters = p.io_counters()
                            usage.io_read_bytes = io_counters.read_bytes
                            usage.io_write_bytes = io_counters.write_bytes
                        except (psutil.AccessDenied, AttributeError):
                            pass

                        # 更新墙钟时间
                        usage.wall_time_seconds = (
                            datetime.now() - start_time
                        ).total_seconds()

                        # 检查限制
                        if limits and on_limit_exceeded:
                            exceeded = self._check_limits(usage, limits)
                            if exceeded:
                                logger.warning(
                                    f"资源超限: {task_id}, reason={exceeded}"
                                )
                                if asyncio.iscoroutinefunction(on_limit_exceeded):
                                    await on_limit_exceeded(task_id, exceeded)
                                else:
                                    on_limit_exceeded(task_id, exceeded)
                                break

                    except psutil.NoSuchProcess:
                        break

                    await asyncio.sleep(self._check_interval)

            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.debug(f"资源监控异常: {e}")

        async with self._lock:
            # 取消已存在的监控
            if task_id in self._monitors:
                self._monitors[task_id].cancel()

            # 创建新的监控任务
            self._monitors[task_id] = asyncio.create_task(monitor())

    async def stop_monitoring(self, task_id: str) -> ResourceUsage | None:
        """
        停止监控进程

        Args:
            task_id: 任务 ID

        Returns:
            资源使用情况
        """
        async with self._lock:
            if task_id in self._monitors:
                self._monitors[task_id].cancel()
                del self._monitors[task_id]

            return self._usage.pop(task_id, None)

    def get_usage(self, task_id: str) -> ResourceUsage | None:
        """
        获取资源使用情况

        Args:
            task_id: 任务 ID

        Returns:
            资源使用情况
        """
        return self._usage.get(task_id)

    def _check_limits(
        self, usage: ResourceUsage, limits: ResourceLimits
    ) -> str | None:
        """
        检查是否超限

        Args:
            usage: 资源使用情况
            limits: 资源限制

        Returns:
            超限原因或 None
        """
        if limits.cpu_limit_seconds > 0 and usage.cpu_time_seconds > limits.cpu_limit_seconds:
            return f"cpu_limit ({usage.cpu_time_seconds:.1f}s > {limits.cpu_limit_seconds}s)"

        if limits.memory_limit_mb > 0 and usage.memory_rss_mb > limits.memory_limit_mb:
            return f"memory_limit ({usage.memory_rss_mb:.1f}MB > {limits.memory_limit_mb}MB)"

        return None


class ProcessTerminator:
    """
    进程终止器

    安全地终止进程（SIGTERM -> grace period -> SIGKILL）。

    Requirements: 7.3
    """

    def __init__(self, default_grace_period: int = 10):
        """
        初始化进程终止器

        Args:
            default_grace_period: 默认 grace period（秒）
        """
        self._default_grace_period = default_grace_period

    async def terminate(
        self,
        process: asyncio.subprocess.Process,
        grace_period: int | None = None,
    ) -> int:
        """
        终止进程

        Args:
            process: 进程对象
            grace_period: grace period（秒）

        Returns:
            退出码
        """
        if process.returncode is not None:
            return process.returncode

        grace = grace_period or self._default_grace_period

        try:
            # 发送 SIGTERM
            process.terminate()
            logger.debug(f"发送 SIGTERM: pid={process.pid}")

            try:
                await asyncio.wait_for(process.wait(), timeout=grace)
                return process.returncode or -signal.SIGTERM
            except TimeoutError:
                pass

            # 发送 SIGKILL
            process.kill()
            logger.debug(f"发送 SIGKILL: pid={process.pid}")
            await process.wait()

            return process.returncode or -signal.SIGKILL

        except ProcessLookupError:
            return process.returncode or 0
        except Exception as e:
            logger.error(f"终止进程失败: {e}")
            return -1

    async def terminate_tree(
        self,
        pid: int,
        grace_period: int | None = None,
    ) -> None:
        """
        终止进程树（包括子进程）

        Args:
            pid: 进程 ID
            grace_period: grace period（秒）
        """
        if not HAS_PSUTIL:
            logger.warning("psutil 不可用，无法终止进程树")
            return

        grace = grace_period or self._default_grace_period

        try:
            parent = psutil.Process(pid)
            children = parent.children(recursive=True)

            # 先终止子进程
            for child in children:
                with contextlib.suppress(psutil.NoSuchProcess):
                    child.terminate()

            # 等待子进程退出
            _, alive = psutil.wait_procs(children, timeout=grace)

            # 强制终止未退出的子进程
            for p in alive:
                with contextlib.suppress(psutil.NoSuchProcess):
                    p.kill()

            # 终止父进程
            try:
                parent.terminate()
                parent.wait(timeout=grace)
            except psutil.TimeoutExpired:
                parent.kill()
            except psutil.NoSuchProcess:
                pass

        except psutil.NoSuchProcess:
            pass
        except Exception as e:
            logger.error(f"终止进程树失败: {e}")


# 便捷函数


async def with_timeout(
    coro,
    timeout: int,
    grace_period: int = 10,
    on_timeout: Any = None,
):
    """
    带超时的协程执行

    Args:
        coro: 协程
        timeout: 超时时间（秒）
        grace_period: grace period（秒）
        on_timeout: 超时回调

    Returns:
        协程结果

    Raises:
        asyncio.TimeoutError: 超时
    """
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except TimeoutError:
        if on_timeout:
            if asyncio.iscoroutinefunction(on_timeout):
                await on_timeout()
            else:
                on_timeout()
        raise


def get_process_limits() -> dict[str, Any]:
    """
    获取当前进程的资源限制

    Returns:
        资源限制字典
    """
    limits = {}

    try:
        import resource

        # 获取各种限制
        limits["max_open_files"] = resource.getrlimit(resource.RLIMIT_NOFILE)
        limits["max_processes"] = resource.getrlimit(resource.RLIMIT_NPROC)
        limits["max_memory"] = resource.getrlimit(resource.RLIMIT_AS)
        limits["max_cpu_time"] = resource.getrlimit(resource.RLIMIT_CPU)
        limits["max_file_size"] = resource.getrlimit(resource.RLIMIT_FSIZE)

    except (ImportError, AttributeError):
        pass

    return limits


def set_process_limits(
    max_cpu_time: int | None = None,
    max_memory_mb: int | None = None,
    max_file_size_mb: int | None = None,
) -> None:
    """
    设置当前进程的资源限制（仅 Unix）

    Args:
        max_cpu_time: 最大 CPU 时间（秒）
        max_memory_mb: 最大内存（MB）
        max_file_size_mb: 最大文件大小（MB）
    """
    try:
        import resource

        if max_cpu_time is not None:
            resource.setrlimit(resource.RLIMIT_CPU, (max_cpu_time, max_cpu_time))

        if max_memory_mb is not None:
            max_bytes = max_memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (max_bytes, max_bytes))

        if max_file_size_mb is not None:
            max_bytes = max_file_size_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_FSIZE, (max_bytes, max_bytes))

    except (ImportError, AttributeError, ValueError) as e:
        logger.warning(f"设置资源限制失败: {e}")
