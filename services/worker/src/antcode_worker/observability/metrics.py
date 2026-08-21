"""Worker 自身的 Prometheus 指标导出。

CPU / 内存 / 磁盘三个百分比**不在这里采集**，而是向心跳采集器要一份快照。本模块
原本自己调 psutil，于是同一台 Worker 的同一个概念有了两个可分叉的真源，而且已经
分叉：真机实测 ``antcode-worker`` 的 ``antcode_worker_memory_percent`` 报 7.7（宿主
31.34GiB 的占比），资源页与 ``docker stats`` 报 2.4（容器 4GiB 的占比）；
``antcode_worker_cpu_percent`` 更直白——同一宿主上三台 Worker 报出**完全相同**的
26.7，因为那根本不是某一台 Worker 的数字。

心跳采集器按 cgroup 额度换算（见 ``heartbeat.metric_probes``），是唯一有依据的口径，
所以由它单方向供数：``MetricsCollector`` 必须被注入一个来源，构造不出"自己读 psutil"
的实例。
"""

from __future__ import annotations

import time
from typing import Any, Protocol

from antcode_worker.heartbeat.metric_models import SystemMetrics


class SystemMetricsSource(Protocol):
    """心跳采集器的只读切面。"""

    async def collect(self, use_cache: bool = True) -> SystemMetrics: ...


class MetricsCollector:
    """把计数器、仪表与心跳快照渲染成 Prometheus 文本。"""

    def __init__(self, system_metrics: SystemMetricsSource):
        self._system_metrics = system_metrics
        self._counters: dict[str, int] = {}
        self._gauges: dict[str, float] = {}
        self._start_time = time.time()

    def inc(self, name: str, value: int = 1) -> None:
        """增加计数器"""
        self._counters[name] = self._counters.get(name, 0) + value

    def set(self, name: str, value: float) -> None:
        """设置仪表值"""
        self._gauges[name] = value

    async def get_system_metrics(self) -> dict[str, float]:
        """三个百分比转发心跳快照，一个字都不自己算。

        走缓存（TTL 1s）：/metrics 被 Prometheus 按秒抓取，每次重采一遍 cgroup 与
        psutil 只会让导出端自己变成负载来源。
        """
        snapshot = await self._system_metrics.collect()
        return {
            "cpu_percent": snapshot.cpu.percent,
            "memory_percent": snapshot.memory.percent,
            "disk_percent": snapshot.disk.percent,
        }

    async def get_all(self) -> dict[str, Any]:
        """获取所有指标"""
        return {
            "uptime_seconds": time.time() - self._start_time,
            **self._counters,
            **self._gauges,
            **await self.get_system_metrics(),
        }

    async def to_prometheus(self) -> str:
        """导出 Prometheus 格式"""
        metrics = await self.get_all()
        return "\n".join(
            f"antcode_worker_{name} {value}" for name, value in metrics.items() if isinstance(value, (int, float))
        )


__all__ = ["MetricsCollector", "SystemMetricsSource"]
