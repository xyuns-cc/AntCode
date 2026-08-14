"""Collect host and Worker execution metrics."""

import asyncio
import platform
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from loguru import logger

from antcode_worker.heartbeat.metric_models import (
    CPUMetrics,
    DiskMetrics,
    MemoryMetrics,
    NetworkMetrics,
    SystemMetrics,
    WorkerMetrics,
)
from antcode_worker.heartbeat.spider_stats import SpiderStatsCollectorMixin

try:
    import psutil

    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False
    logger.warning("psutil 未安装，系统指标采集将受限")


if TYPE_CHECKING:
    from antcode_worker.engine.scheduler import Scheduler
    from antcode_worker.engine.state import StateManager


class SystemMetricsCollector(SpiderStatsCollectorMixin):
    """Collect CPU, memory, disk, network, and execution metrics."""

    def __init__(
        self,
        disk_path: str = "/",
        max_slots: int = 5,
    ):
        self._disk_path = disk_path
        self._max_slots = max_slots

        # 网络速率计算
        self._last_net_io: tuple[int, int, float] | None = None

        # Worker 指标
        self._total_tasks_executed = 0
        self._executed_project_ids: set[str] = set()
        self._last_heartbeat_ts = 0.0
        self._reconnect_count = 0
        self._started_at = time.monotonic()
        self._env_count_provider: Callable[[], int] | None = None

        # 引擎指标提供者
        self._state_manager: StateManager | None = None
        self._scheduler: Scheduler | None = None

        # 缓存
        self._cached_metrics: SystemMetrics | None = None
        self._cache_ttl = 1.0  # 缓存 1 秒
        self._last_collect_time = 0.0

    def set_state_manager(self, state_manager: "StateManager") -> None:
        """设置状态管理器"""
        self._state_manager = state_manager

    def set_scheduler(self, scheduler: "Scheduler") -> None:
        """设置调度器"""
        self._scheduler = scheduler

    def set_max_slots(self, max_slots: int) -> None:
        """设置最大任务槽位"""
        self._max_slots = max_slots
        self._cached_metrics = None
        self._last_collect_time = 0.0

    def set_env_count_provider(self, provider: Callable[[], int]) -> None:
        self._env_count_provider = provider

    def record_task_executed(self, project_id: str) -> None:
        """Record one real execution and its non-empty project identity."""
        self._total_tasks_executed += 1
        if project_id:
            self._executed_project_ids.add(project_id)

    def update_heartbeat_ts(self, ts: float | None = None) -> None:
        """更新心跳时间戳"""
        self._last_heartbeat_ts = ts or time.time()

    def increment_reconnect_count(self) -> None:
        """增加重连计数"""
        self._reconnect_count += 1

    def reset_reconnect_count(self) -> None:
        """重置重连计数"""
        self._reconnect_count = 0

    async def collect(self, use_cache: bool = True) -> SystemMetrics:
        """
        采集系统指标

        Args:
            use_cache: 是否使用缓存

        Returns:
            系统指标
        """
        now = time.time()

        # 检查缓存
        if use_cache and self._cached_metrics and now - self._last_collect_time < self._cache_ttl:
            return self._cached_metrics

        metrics = SystemMetrics(timestamp=now)

        # 采集各项指标
        metrics.cpu = await self._collect_cpu()
        metrics.memory = await self._collect_memory()
        metrics.disk = await self._collect_disk()
        metrics.network = await self._collect_network()
        metrics.worker = await self._collect_worker()

        # 更新缓存
        self._cached_metrics = metrics
        self._last_collect_time = now

        return metrics

    async def _collect_cpu(self) -> CPUMetrics:
        """采集 CPU 指标"""
        metrics = CPUMetrics()

        if not HAS_PSUTIL:
            return metrics

        try:
            # CPU 使用率（非阻塞）
            metrics.percent = await asyncio.to_thread(psutil.cpu_percent, interval=None)
            metrics.count = psutil.cpu_count() or 1

            # 负载（仅 Unix）
            if hasattr(psutil, "getloadavg"):
                load = psutil.getloadavg()
                metrics.load_avg_1m = round(load[0], 2)
                metrics.load_avg_5m = round(load[1], 2)
                metrics.load_avg_15m = round(load[2], 2)

        except Exception as e:
            logger.debug(f"采集 CPU 指标失败: {e}")

        return metrics

    async def _collect_memory(self) -> MemoryMetrics:
        """采集内存指标"""
        metrics = MemoryMetrics()

        if not HAS_PSUTIL:
            return metrics

        try:
            mem = psutil.virtual_memory()
            metrics.percent = round(mem.percent, 1)
            metrics.total_mb = round(mem.total / (1024 * 1024), 1)
            metrics.available_mb = round(mem.available / (1024 * 1024), 1)
            metrics.used_mb = round(mem.used / (1024 * 1024), 1)

        except Exception as e:
            logger.debug(f"采集内存指标失败: {e}")

        return metrics

    async def _collect_disk(self) -> DiskMetrics:
        """采集磁盘指标"""
        metrics = DiskMetrics()

        if not HAS_PSUTIL:
            return metrics

        try:
            disk = psutil.disk_usage(self._disk_path)
            metrics.percent = round(disk.percent, 1)
            metrics.total_gb = round(disk.total / (1024 * 1024 * 1024), 2)
            metrics.free_gb = round(disk.free / (1024 * 1024 * 1024), 2)
            metrics.used_gb = round(disk.used / (1024 * 1024 * 1024), 2)

        except Exception as e:
            logger.debug(f"采集磁盘指标失败: {e}")

        return metrics

    async def _collect_network(self) -> NetworkMetrics:
        """采集网络指标"""
        metrics = NetworkMetrics()

        if not HAS_PSUTIL:
            return metrics

        try:
            net_io = psutil.net_io_counters()
            now = time.time()

            metrics.bytes_sent = net_io.bytes_sent
            metrics.bytes_recv = net_io.bytes_recv
            metrics.packets_sent = net_io.packets_sent
            metrics.packets_recv = net_io.packets_recv

            # 计算速率
            if self._last_net_io:
                last_sent, last_recv, last_time = self._last_net_io
                elapsed = now - last_time
                if elapsed > 0:
                    metrics.bytes_sent_rate = round((net_io.bytes_sent - last_sent) / elapsed, 1)
                    metrics.bytes_recv_rate = round((net_io.bytes_recv - last_recv) / elapsed, 1)

            self._last_net_io = (net_io.bytes_sent, net_io.bytes_recv, now)

        except Exception as e:
            logger.debug(f"采集网络指标失败: {e}")

        return metrics

    async def _collect_worker(self) -> WorkerMetrics:
        """采集 Worker 特定指标"""
        metrics = WorkerMetrics()

        metrics.max_slots = self._max_slots
        metrics.total_tasks_executed = self._total_tasks_executed
        metrics.project_count = len(self._executed_project_ids)
        metrics.last_heartbeat_ts = self._last_heartbeat_ts
        metrics.reconnect_count = self._reconnect_count
        metrics.uptime_seconds = max(int(time.monotonic() - self._started_at), 0)
        if self._env_count_provider is not None:
            metrics.env_count = await asyncio.to_thread(self._env_count_provider)

        # 从状态管理器获取运行中任务数
        if self._state_manager:
            try:
                metrics.running_slots = await self._state_manager.count_active()
            except Exception as e:
                logger.debug(f"获取运行任务数失败: {e}")

        # 从调度器获取队列深度
        if self._scheduler:
            try:
                metrics.queue_depth = self._scheduler.size
            except Exception as e:
                logger.debug(f"获取队列深度失败: {e}")

        return metrics

    def get_os_info(self) -> dict[str, str]:
        """
        获取操作系统信息

        Returns:
            操作系统信息字典
        """
        return {
            "os_type": platform.system(),
            "os_version": platform.release(),
            "python_version": platform.python_version(),
            "machine_arch": platform.machine(),
        }


# 全局实例
_metrics_collector: SystemMetricsCollector | None = None


def get_metrics_collector() -> SystemMetricsCollector | None:
    """获取全局指标采集器"""
    return _metrics_collector


def init_metrics_collector(
    disk_path: str = "/",
    max_slots: int = 5,
) -> SystemMetricsCollector:
    """Initialize the process-wide metrics collector."""
    global _metrics_collector
    _metrics_collector = SystemMetricsCollector(
        disk_path=disk_path,
        max_slots=max_slots,
    )
    return _metrics_collector
