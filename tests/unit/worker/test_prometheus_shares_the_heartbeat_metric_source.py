"""Worker 的 /metrics 与资源页必须报同一个数，因为它们是同一个概念。

修复前是两个可分叉的真源，而且已经分叉——真机 192.168.1.250 实测：

    $ docker exec antcode-worker curl -s localhost:8001/metrics
    antcode_worker_cpu_percent 26.7
    antcode_worker_memory_percent 7.7        # 宿主 31.34GiB 的占比
    $ docker stats --no-stream antcode-worker
    antcode-worker CPU=0.18% MEM=2.43% (99.61MiB / 4GiB)   # 容器 4GiB 的占比

``antcode_worker_cpu_percent`` 更直白：同一宿主上三台 Worker 报出**完全相同**的
26.7，因为 ``psutil`` 读的根本不是某一台 Worker 的数字。

证伪方式：把 ``MetricsCollector.get_system_metrics`` 改回自己调 psutil（并给
``__init__`` 的来源加回默认值），除对照组外全部变红。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from antcode_worker.app.wiring import _create_observability_server
from antcode_worker.heartbeat.metric_models import CPUMetrics, DiskMetrics, MemoryMetrics, SystemMetrics
from antcode_worker.heartbeat.metrics_assembly import collect_metrics
from antcode_worker.observability.metrics import MetricsCollector

# 真机实测的容器口径（antcode-worker：4GiB 配额、99.61MiB working set）
_CONTAINER_CPU_PERCENT = 0.2
_CONTAINER_MEMORY_PERCENT = 2.4
_CONTAINER_DISK_PERCENT = 68.2
# 同一时刻宿主口径报出来的那三个数
_HOST_MEMORY_PERCENT = 7.7

_FALLBACK_CONCURRENCY = 4
_COUNTER_VALUE = 5
_GAUGE_VALUE = 3.0


class _StubHeartbeatCollector:
    """心跳采集器的最小切面：只需要 collect 与 spider 统计。"""

    def __init__(self) -> None:
        self.collect_calls: list[bool] = []

    async def collect(self, use_cache: bool = True) -> SystemMetrics:
        self.collect_calls.append(use_cache)
        return SystemMetrics(
            cpu=CPUMetrics(percent=_CONTAINER_CPU_PERCENT, count=4),
            memory=MemoryMetrics(percent=_CONTAINER_MEMORY_PERCENT, total_mb=4096.0, used_mb=99.6),
            disk=DiskMetrics(percent=_CONTAINER_DISK_PERCENT),
        )

    def get_spider_stats(self) -> dict:
        return {}


def _prometheus_value(text: str, name: str) -> float:
    for line in text.splitlines():
        metric, _, value = line.partition(" ")
        if metric == name:
            return float(value)
    raise AssertionError(f"{name} 没有出现在 /metrics 输出里:\n{text}")


@pytest.mark.asyncio
async def test_prometheus_percentages_come_from_the_heartbeat_snapshot() -> None:
    """三个百分比一个字都不自己算，全部转发心跳快照。"""
    source = _StubHeartbeatCollector()

    text = await MetricsCollector(source).to_prometheus()

    assert _prometheus_value(text, "antcode_worker_memory_percent") == _CONTAINER_MEMORY_PERCENT
    assert _prometheus_value(text, "antcode_worker_cpu_percent") == _CONTAINER_CPU_PERCENT
    assert _prometheus_value(text, "antcode_worker_disk_percent") == _CONTAINER_DISK_PERCENT
    assert source.collect_calls, "没有向心跳采集器要过数，说明它又自己采了一份"


@pytest.mark.asyncio
async def test_prometheus_and_the_heartbeat_wire_agree_on_memory_percent() -> None:
    """同一份快照的两种渲染必须相等——这才是"收敛到一个数"的定义。"""
    source = _StubHeartbeatCollector()

    text = await MetricsCollector(source).to_prometheus()
    wire = await collect_metrics(source, _FALLBACK_CONCURRENCY)

    assert _prometheus_value(text, "antcode_worker_memory_percent") == wire.memory
    assert _prometheus_value(text, "antcode_worker_cpu_percent") == wire.cpu
    assert wire.memory != _HOST_MEMORY_PERCENT, "心跳侧报了宿主占比，说明对照的是错的那一侧"


def test_metrics_collector_cannot_be_constructed_without_a_source() -> None:
    """结构性保证：造不出"自己读 psutil"的实例，第二个真源就长不出来。"""
    with pytest.raises(TypeError):
        MetricsCollector()  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_observability_server_is_wired_to_the_heartbeat_collector() -> None:
    """一路证到接线：/metrics 端点拿到的就是心跳那一份采集器。"""
    source = _StubHeartbeatCollector()
    transport = SimpleNamespace(is_connected=True, is_running=True)
    engine = SimpleNamespace(get_stats=lambda: {"running": True})

    server = _create_observability_server(transport, engine, source)
    exported = await server.metrics_collector.get_system_metrics()

    assert exported["memory_percent"] == _CONTAINER_MEMORY_PERCENT
    assert source.collect_calls


@pytest.mark.asyncio
async def test_counters_and_gauges_still_export() -> None:
    """对照组（非证伪项）：修复只动三个百分比的来源，通用计数器/仪表面不受影响。"""
    collector = MetricsCollector(_StubHeartbeatCollector())
    collector.inc("tasks_completed", _COUNTER_VALUE)
    collector.set("queue_depth", _GAUGE_VALUE)

    text = await collector.to_prometheus()

    assert _prometheus_value(text, "antcode_worker_tasks_completed") == _COUNTER_VALUE
    assert _prometheus_value(text, "antcode_worker_queue_depth") == _GAUGE_VALUE
    assert _prometheus_value(text, "antcode_worker_uptime_seconds") >= 0
