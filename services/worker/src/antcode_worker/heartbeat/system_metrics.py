"""Collect this Worker's own resource metrics and its execution metrics.

单项指标"怎么读出来"在 ``metric_probes``：CPU（核数与使用率）和内存四件套报的都是
**容器额度**而非宿主 /proc 视图。本模块只负责编排、缓存与 Worker 自身的执行指标。
"""

import asyncio
import platform
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from loguru import logger

from antcode_worker.cpu_usage import ContainerCpuSampler
from antcode_worker.heartbeat.metric_models import (
    EffectiveTaskLimits,
    SystemMetrics,
    WorkerMetrics,
)
from antcode_worker.heartbeat.metric_probes import (
    NetworkRateProbe,
    probe_cpu,
    probe_disk,
    probe_memory,
)
from antcode_worker.heartbeat.spider_stats import SpiderStatsCollectorMixin

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

        # 网络速率与容器 CPU 使用率都要跨采样求差，状态由探针自己持有
        self._network_probe = NetworkRateProbe()
        self._cpu_sampler = ContainerCpuSampler()

        # Worker 指标
        self._total_tasks_executed = 0
        self._executed_project_ids: set[str] = set()
        self._last_heartbeat_ts = 0.0
        self._reconnect_count = 0
        self._started_at = time.monotonic()
        self._env_count_provider: Callable[[], int] | None = None
        # 生效限额只有引擎知道，且会被 config_update 就地改写。这里存**提供者**
        # 而不是快照，避免复制出一份会过期的值——过期的限额就是这个页面原本的 bug。
        self._task_limits_provider: Callable[[], EffectiveTaskLimits] | None = None

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

    def set_task_limits_provider(self, provider: Callable[[], EffectiveTaskLimits]) -> None:
        """接上生效限额的实时来源（引擎的资源策略）。"""
        self._task_limits_provider = provider

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
        """采集系统指标"""
        now = time.time()

        # 检查缓存
        if use_cache and self._cached_metrics and now - self._last_collect_time < self._cache_ttl:
            return self._cached_metrics

        metrics = SystemMetrics(timestamp=now)

        # 采集各项指标
        metrics.cpu = await probe_cpu(self._cpu_sampler)
        metrics.memory = await probe_memory()
        metrics.disk = await probe_disk(self._disk_path)
        metrics.network = await self._network_probe.probe()
        metrics.worker = await self._collect_worker()

        # 更新缓存
        self._cached_metrics = metrics
        self._last_collect_time = now

        return metrics

    async def _collect_worker(self) -> WorkerMetrics:
        """采集 Worker 特定指标"""
        metrics = WorkerMetrics()

        metrics.max_slots = self._max_slots
        if self._task_limits_provider is not None:
            limits = self._task_limits_provider()
            metrics.task_memory_limit_mb = limits.memory_limit_mb
            metrics.task_cpu_time_limit_sec = limits.cpu_time_limit_sec
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
        """获取操作系统信息"""
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
