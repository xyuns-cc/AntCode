"""
系统指标采集器

采集 CPU/memory/disk/network 以及 Worker 特定指标（running slots, queue depth）。

Requirements: 10.2, 10.4
"""

import asyncio
import platform
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from loguru import logger

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False
    logger.warning("psutil 未安装，系统指标采集将受限")


if TYPE_CHECKING:
    from antcode_worker.engine.scheduler import Scheduler
    from antcode_worker.engine.state import StateManager


class EngineMetricsProvider(Protocol):
    """引擎指标提供者协议"""

    async def get_running_count(self) -> int:
        """获取正在运行的任务数"""
        ...

    async def get_queue_depth(self) -> int:
        """获取队列深度"""
        ...


@dataclass
class CPUMetrics:
    """CPU 指标"""
    percent: float = 0.0           # CPU 使用率 (0-100)
    count: int = 0                 # CPU 核心数
    load_avg_1m: float = 0.0       # 1 分钟负载
    load_avg_5m: float = 0.0       # 5 分钟负载
    load_avg_15m: float = 0.0      # 15 分钟负载


@dataclass
class MemoryMetrics:
    """内存指标"""
    percent: float = 0.0           # 内存使用率 (0-100)
    total_mb: float = 0.0          # 总内存 (MB)
    available_mb: float = 0.0      # 可用内存 (MB)
    used_mb: float = 0.0           # 已用内存 (MB)


@dataclass
class DiskMetrics:
    """磁盘指标"""
    percent: float = 0.0           # 磁盘使用率 (0-100)
    total_gb: float = 0.0          # 总容量 (GB)
    free_gb: float = 0.0           # 可用容量 (GB)
    used_gb: float = 0.0           # 已用容量 (GB)


@dataclass
class NetworkMetrics:
    """网络指标"""
    bytes_sent: int = 0            # 发送字节数
    bytes_recv: int = 0            # 接收字节数
    packets_sent: int = 0          # 发送包数
    packets_recv: int = 0          # 接收包数
    bytes_sent_rate: float = 0.0   # 发送速率 (bytes/s)
    bytes_recv_rate: float = 0.0   # 接收速率 (bytes/s)


@dataclass
class WorkerMetrics:
    """Worker 特定指标"""
    running_slots: int = 0         # 正在运行的任务槽位
    max_slots: int = 0             # 最大任务槽位
    queue_depth: int = 0           # 队列深度
    total_tasks_executed: int = 0  # 总执行任务数
    last_heartbeat_ts: float = 0.0 # 上次心跳时间戳
    reconnect_count: int = 0       # 重连次数


@dataclass
class SystemMetrics:
    """系统指标汇总"""
    cpu: CPUMetrics = field(default_factory=CPUMetrics)
    memory: MemoryMetrics = field(default_factory=MemoryMetrics)
    disk: DiskMetrics = field(default_factory=DiskMetrics)
    network: NetworkMetrics = field(default_factory=NetworkMetrics)
    worker: WorkerMetrics = field(default_factory=WorkerMetrics)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "cpu": {
                "percent": self.cpu.percent,
                "count": self.cpu.count,
                "load_avg_1m": self.cpu.load_avg_1m,
                "load_avg_5m": self.cpu.load_avg_5m,
                "load_avg_15m": self.cpu.load_avg_15m,
            },
            "memory": {
                "percent": self.memory.percent,
                "total_mb": self.memory.total_mb,
                "available_mb": self.memory.available_mb,
                "used_mb": self.memory.used_mb,
            },
            "disk": {
                "percent": self.disk.percent,
                "total_gb": self.disk.total_gb,
                "free_gb": self.disk.free_gb,
                "used_gb": self.disk.used_gb,
            },
            "network": {
                "bytes_sent": self.network.bytes_sent,
                "bytes_recv": self.network.bytes_recv,
                "packets_sent": self.network.packets_sent,
                "packets_recv": self.network.packets_recv,
                "bytes_sent_rate": self.network.bytes_sent_rate,
                "bytes_recv_rate": self.network.bytes_recv_rate,
            },
            "worker": {
                "running_slots": self.worker.running_slots,
                "max_slots": self.worker.max_slots,
                "queue_depth": self.worker.queue_depth,
                "total_tasks_executed": self.worker.total_tasks_executed,
                "last_heartbeat_ts": self.worker.last_heartbeat_ts,
                "reconnect_count": self.worker.reconnect_count,
            },
            "timestamp": self.timestamp,
        }


class SystemMetricsCollector:
    """
    系统指标采集器

    采集 CPU/memory/disk/network 以及 Worker 特定指标。

    Requirements: 10.2, 10.4
    """

    def __init__(
        self,
        disk_path: str = "/",
        max_slots: int = 5,
    ):
        """
        初始化采集器

        Args:
            disk_path: 磁盘监控路径
            max_slots: 最大任务槽位
        """
        self._disk_path = disk_path
        self._max_slots = max_slots

        # 网络速率计算
        self._last_net_io: tuple[int, int, float] | None = None

        # Worker 指标
        self._total_tasks_executed = 0
        self._last_heartbeat_ts = 0.0
        self._reconnect_count = 0

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

    def increment_tasks_executed(self) -> None:
        """增加已执行任务计数"""
        self._total_tasks_executed += 1

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
            metrics.percent = await asyncio.to_thread(
                psutil.cpu_percent, interval=None
            )
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
                    metrics.bytes_sent_rate = round(
                        (net_io.bytes_sent - last_sent) / elapsed, 1
                    )
                    metrics.bytes_recv_rate = round(
                        (net_io.bytes_recv - last_recv) / elapsed, 1
                    )

            self._last_net_io = (net_io.bytes_sent, net_io.bytes_recv, now)

        except Exception as e:
            logger.debug(f"采集网络指标失败: {e}")

        return metrics

    async def _collect_worker(self) -> WorkerMetrics:
        """采集 Worker 特定指标"""
        metrics = WorkerMetrics()

        metrics.max_slots = self._max_slots
        metrics.total_tasks_executed = self._total_tasks_executed
        metrics.last_heartbeat_ts = self._last_heartbeat_ts
        metrics.reconnect_count = self._reconnect_count

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

    def get_metrics(self) -> dict[str, Any]:
        """
        获取指标（同步接口，用于心跳）

        Returns:
            指标字典
        """
        if not HAS_PSUTIL:
            return {
                "cpu": 0.0,
                "memory": 0.0,
                "disk": 0.0,
                "runningTasks": 0,
                "maxConcurrentTasks": self._max_slots,
                "taskCount": self._total_tasks_executed,
            }

        try:
            cpu = psutil.cpu_percent(interval=None)
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage(self._disk_path)

            return {
                "cpu": round(cpu, 1),
                "memory": round(mem.percent, 1),
                "disk": round(disk.percent, 1),
                "runningTasks": 0,  # 需要异步获取
                "maxConcurrentTasks": self._max_slots,
                "taskCount": self._total_tasks_executed,
            }
        except Exception as e:
            logger.debug(f"获取指标失败: {e}")
            return {
                "cpu": 0.0,
                "memory": 0.0,
                "disk": 0.0,
                "runningTasks": 0,
                "maxConcurrentTasks": self._max_slots,
                "taskCount": self._total_tasks_executed,
            }

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

    def get_spider_stats(self) -> dict | None:
        """
        获取爬虫统计（占位）

        Returns:
            爬虫统计或 None
        """
        # 爬虫统计由 spider plugin 提供
        return None


# 全局实例
_metrics_collector: SystemMetricsCollector | None = None


def get_metrics_collector() -> SystemMetricsCollector | None:
    """获取全局指标采集器"""
    return _metrics_collector


def init_metrics_collector(
    disk_path: str = "/",
    max_slots: int = 5,
) -> SystemMetricsCollector:
    """
    初始化全局指标采集器

    Args:
        disk_path: 磁盘监控路径
        max_slots: 最大任务槽位

    Returns:
        指标采集器实例
    """
    global _metrics_collector
    _metrics_collector = SystemMetricsCollector(
        disk_path=disk_path,
        max_slots=max_slots,
    )
    return _metrics_collector
