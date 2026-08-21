"""单项指标探针。从 ``system_metrics.py`` 拆出：采集器负责编排、缓存与 Worker
自身的执行指标，"一个数怎么读出来"是另一件事，而且只有这部分需要区分宿主视图
与容器额度。

CPU 核数与内存总额报的是**本容器的额度**，不是宿主 /proc 的数字。资源页、
Monitor 详情抽屉与调度器可用性门禁读的都是这里报上去的值：报宿主数字会让容量
规划高估一个数量级（真机实测同一台 Worker：宿主 8 核/31.34GiB，容器额度 2 核/3GiB），
也会让"这台还能不能再接一个任务"用宿主的忙闲程度作答。来源与 fail-closed 语义
统一在 ``resource_budget`` / ``resource_usage``，本模块只负责把宿主快照喂给它们。
"""

from __future__ import annotations

import asyncio
import time

from loguru import logger

from antcode_worker.heartbeat.metric_models import (
    CPUMetrics,
    DiskMetrics,
    MemoryMetrics,
    NetworkMetrics,
)
from antcode_worker.resource_budget import resolve_cpu_budget, resolve_memory_budget
from antcode_worker.resource_usage import HostMemory, resolve_memory_usage

try:
    import psutil

    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False
    logger.warning("psutil 未安装，系统指标采集将受限")

_LOAD_DIGITS = 2
_PERCENT_DIGITS = 1
_GB_DIGITS = 2
_RATE_DIGITS = 1
_BYTES_PER_MIB = 1024 * 1024
_BYTES_PER_GIB = 1024 * _BYTES_PER_MIB


async def probe_cpu() -> CPUMetrics:
    """使用率仍是宿主视图，核数必须是容器配额。

    ``count`` 走 ``resolve_cpu_budget`` 而不是 ``psutil.cpu_count()``：后者在容器里
    读到的是宿主 nproc（真机 8），与容器 ``cpus=2`` 无关。配额解析失败会抛
    ``ResourceBudgetError``，故意放在 try 之外——把"核数不可知"吞成 0 或悄悄退回
    宿主值，正是本次要消灭的那类静默失败。

    ``percent`` 保持宿主口径：它是利用率而非容量，不参与容量规划的乘法，且容器
    自己的 CPU 用量需要跨采样求差值，与本次修复不是同一件事。
    """
    metrics = CPUMetrics()
    if not HAS_PSUTIL:
        return metrics

    metrics.count = resolve_cpu_budget(psutil.cpu_count() or 1).cores
    try:
        metrics.percent = await asyncio.to_thread(psutil.cpu_percent, interval=None)
        _fill_load_average(metrics)
    except Exception as e:  # noqa: BLE001 - psutil 抖动不该拖垮整份心跳
        logger.debug(f"采集 CPU 使用率失败: {e}")
    return metrics


def _fill_load_average(metrics: CPUMetrics) -> None:
    if not hasattr(psutil, "getloadavg"):
        return
    load = psutil.getloadavg()
    metrics.load_avg_1m = round(load[0], _LOAD_DIGITS)
    metrics.load_avg_5m = round(load[1], _LOAD_DIGITS)
    metrics.load_avg_15m = round(load[2], _LOAD_DIGITS)


def _host_memory() -> HostMemory:
    mem = psutil.virtual_memory()
    return HostMemory(
        total_bytes=int(mem.total),
        used_bytes=int(mem.used),
        available_bytes=int(mem.available),
        percent=round(mem.percent, _PERCENT_DIGITS),
    )


async def probe_memory() -> MemoryMetrics:
    """总额/已用/可用/占比四个数一起取自同一层（容器 cgroup 优先）。

    这里没有 ``except``：内存额度不可知时报一组零，与报宿主值一样是在给下游一个
    没有依据的数——资源页、调度门禁和 Worker 自己的过载保护全都吃这四个值。让
    ``ResourceBudgetError`` 一路冒到心跳上去，是唯一能被看见的失败方式。
    """
    metrics = MemoryMetrics()
    if not HAS_PSUTIL:
        return metrics

    host = _host_memory()
    usage = resolve_memory_usage(resolve_memory_budget(host.total_bytes), host)
    metrics.percent = usage.percent
    metrics.total_mb = usage.total_mb
    metrics.available_mb = usage.available_mb
    metrics.used_mb = usage.used_mb
    return metrics


async def probe_disk(disk_path: str) -> DiskMetrics:
    """磁盘按挂载点读，容器里 ``/`` 本就是容器自己的 overlay，无需 cgroup 换算。"""
    metrics = DiskMetrics()
    if not HAS_PSUTIL:
        return metrics

    try:
        disk = psutil.disk_usage(disk_path)
        metrics.percent = round(disk.percent, _PERCENT_DIGITS)
        metrics.total_gb = round(disk.total / _BYTES_PER_GIB, _GB_DIGITS)
        metrics.free_gb = round(disk.free / _BYTES_PER_GIB, _GB_DIGITS)
        metrics.used_gb = round(disk.used / _BYTES_PER_GIB, _GB_DIGITS)
    except Exception as e:  # noqa: BLE001 - 挂载点瞬时不可读不该拖垮整份心跳
        logger.debug(f"采集磁盘指标失败: {e}")
    return metrics


class NetworkRateProbe:
    """网络速率要跨两次采样求差，因此探针自己持有上一份计数。"""

    def __init__(self) -> None:
        self._last: tuple[int, int, float] | None = None

    async def probe(self) -> NetworkMetrics:
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
            self._fill_rates(metrics, now)
            self._last = (net_io.bytes_sent, net_io.bytes_recv, now)
        except Exception as e:  # noqa: BLE001 - psutil 抖动不该拖垮整份心跳
            logger.debug(f"采集网络指标失败: {e}")
        return metrics

    def _fill_rates(self, metrics: NetworkMetrics, now: float) -> None:
        if self._last is None:
            return
        last_sent, last_recv, last_time = self._last
        elapsed = now - last_time
        if elapsed <= 0:
            return
        metrics.bytes_sent_rate = round((metrics.bytes_sent - last_sent) / elapsed, _RATE_DIGITS)
        metrics.bytes_recv_rate = round((metrics.bytes_recv - last_recv) / elapsed, _RATE_DIGITS)


__all__ = [
    "HAS_PSUTIL",
    "NetworkRateProbe",
    "probe_cpu",
    "probe_disk",
    "probe_memory",
]
