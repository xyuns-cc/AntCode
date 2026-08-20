"""采集快照 → 心跳 wire 形状（``reporter_models.Metrics``）的装配。

从 ``reporter.py`` 拆出：上报器负责续期节拍与重连状态机，指标装配是另一件事，
而且它是唯一需要随 ``contracts/proto/common.proto`` 的 ``Metrics`` 一起改的部分。
"""

from __future__ import annotations

from typing import Any

from antcode_worker.heartbeat.reporter_models import Metrics, SpiderStats

MIB_IN_BYTES = 1024 * 1024
GIB_IN_BYTES = 1024 * MIB_IN_BYTES

_PERCENT_MIN = 0.0
_PERCENT_MAX = 100.0
_PERCENT_DIGITS = 1


def bounded_percent(value: float) -> float:
    return round(max(_PERCENT_MIN, min(_PERCENT_MAX, value)), _PERCENT_DIGITS)


async def collect_metrics(collector: Any, fallback_max_concurrent: int) -> Metrics:
    """把采集器快照装成心跳 wire 形状。

    没有采集器时只报得出并发（上报器自己的配置），其余字段留默认值——包括生效
    限额的 0，它在控制面被读成"未知"。这里不许拿配置值顶替生效值：限额由执行面
    按 cgroup 预算收敛，两者经常不相等，编一个就是在报假数。
    """
    if collector is None:
        return Metrics(max_concurrent_tasks=fallback_max_concurrent)
    collected = await collector.collect(use_cache=False)
    worker = collected.worker
    return Metrics(
        cpu=bounded_percent(collected.cpu.percent),
        memory=bounded_percent(collected.memory.percent),
        disk=bounded_percent(collected.disk.percent),
        running_tasks=worker.running_slots,
        max_concurrent_tasks=worker.max_slots or fallback_max_concurrent,
        task_count=worker.total_tasks_executed,
        project_count=worker.project_count,
        env_count=worker.env_count,
        queued_tasks=worker.queue_depth,
        cpu_cores=collected.cpu.count,
        memory_total_bytes=int(collected.memory.total_mb * MIB_IN_BYTES),
        memory_used_bytes=int(collected.memory.used_mb * MIB_IN_BYTES),
        memory_available_bytes=int(collected.memory.available_mb * MIB_IN_BYTES),
        disk_total_bytes=int(collected.disk.total_gb * GIB_IN_BYTES),
        disk_used_bytes=int(collected.disk.used_gb * GIB_IN_BYTES),
        disk_free_bytes=int(collected.disk.free_gb * GIB_IN_BYTES),
        uptime_seconds=worker.uptime_seconds,
        task_memory_limit_mb=worker.task_memory_limit_mb,
        task_cpu_time_limit_sec=worker.task_cpu_time_limit_sec,
        spider_stats=build_spider_stats(collector),
    )


def build_spider_stats(collector: Any) -> SpiderStats | None:
    if collector is None:
        return None
    stats = collector.get_spider_stats()
    if not stats:
        return None
    return SpiderStats(
        request_count=stats.get("request_count", 0),
        response_count=stats.get("response_count", 0),
        item_scraped_count=stats.get("item_scraped_count", 0),
        error_count=stats.get("error_count", 0),
        avg_latency_ms=stats.get("avg_latency_ms", 0.0),
        requests_per_minute=stats.get("requests_per_minute", 0.0),
        status_codes=stats.get("status_codes", {}),
        domain_stats=stats.get("domain_stats", []),
    )


__all__ = [
    "GIB_IN_BYTES",
    "MIB_IN_BYTES",
    "bounded_percent",
    "build_spider_stats",
    "collect_metrics",
]
