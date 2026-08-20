"""Concurrency resize behavior for Worker executors and heartbeat capacity."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_worker.app.engine_wiring import create_engine
from antcode_worker.executor.base import ExecutorConfig
from antcode_worker.executor.concurrency import ResizableConcurrencyGate
from antcode_worker.executor.sandbox import NoOpSandbox, SandboxExecutor
from antcode_worker.heartbeat.reporter import HeartbeatReporter
from antcode_worker.heartbeat.system_metrics import SystemMetricsCollector

INITIAL_CAPACITY = 2
EXPANDED_CAPACITY = 4
UPDATED_HEARTBEAT_CAPACITY = 5
STARTUP_MEMORY_LIMIT_MB = 512
STARTUP_CPU_LIMIT_SEC = 60
RESIZED_MEMORY_LIMIT_MB = 1024


async def _occupy(gate: ResizableConcurrencyGate, entered: asyncio.Event, release: asyncio.Event) -> None:
    async with gate.slot():
        entered.set()
        await release.wait()


@pytest.mark.asyncio
async def test_gate_shrink_waits_for_inflight_work_before_new_admission() -> None:
    gate = ResizableConcurrencyGate(2)
    entered = [asyncio.Event() for _ in range(3)]
    releases = [asyncio.Event() for _ in range(3)]
    tasks = [asyncio.create_task(_occupy(gate, entered[index], releases[index])) for index in range(2)]
    await asyncio.gather(*(event.wait() for event in entered[:2]))

    await gate.resize(1)
    third = asyncio.create_task(_occupy(gate, entered[2], releases[2]))
    tasks.append(third)
    await asyncio.sleep(0)
    assert not entered[2].is_set()

    releases[0].set()
    await asyncio.sleep(0)
    assert not entered[2].is_set()
    releases[1].set()
    await asyncio.wait_for(entered[2].wait(), timeout=0.1)

    releases[2].set()
    await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_gate_expansion_unblocks_waiting_work() -> None:
    gate = ResizableConcurrencyGate(1)
    entered = [asyncio.Event(), asyncio.Event()]
    releases = [asyncio.Event(), asyncio.Event()]
    tasks = [asyncio.create_task(_occupy(gate, entered[index], releases[index])) for index in range(2)]
    await entered[0].wait()
    await asyncio.sleep(0)
    assert not entered[1].is_set()

    await gate.resize(2)
    await asyncio.wait_for(entered[1].wait(), timeout=0.1)

    for event in releases:
        event.set()
    await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_sandbox_resize_updates_outer_and_process_gates() -> None:
    executor = SandboxExecutor(
        config=ExecutorConfig(max_concurrent=INITIAL_CAPACITY),
        sandbox_provider=NoOpSandbox(),
    )

    await executor.resize_concurrency(EXPANDED_CAPACITY)

    assert executor._concurrency_gate.limit == EXPANDED_CAPACITY
    assert executor._process_executor._concurrency_gate.limit == EXPANDED_CAPACITY
    assert executor.config.max_concurrent == EXPANDED_CAPACITY
    assert executor._process_executor.config.max_concurrent == EXPANDED_CAPACITY


@pytest.mark.asyncio
async def test_metrics_capacity_update_invalidates_cached_heartbeat() -> None:
    collector = SystemMetricsCollector(max_slots=INITIAL_CAPACITY)
    first = await collector.collect(use_cache=True)
    assert first.worker.max_slots == INITIAL_CAPACITY

    collector.set_max_slots(UPDATED_HEARTBEAT_CAPACITY)
    second = await collector.collect(use_cache=True)

    assert second is not first
    assert second.worker.max_slots == UPDATED_HEARTBEAT_CAPACITY


@pytest.mark.asyncio
async def test_reporter_fallback_capacity_tracks_live_update() -> None:
    reporter = HeartbeatReporter(
        transport=object(),
        worker_id="worker-1",
        max_concurrent_tasks=INITIAL_CAPACITY,
    )

    reporter.set_max_concurrent_tasks(UPDATED_HEARTBEAT_CAPACITY)
    metrics = await reporter._get_metrics()

    assert metrics.max_concurrent_tasks == UPDATED_HEARTBEAT_CAPACITY


def _wire(collector: SystemMetricsCollector, reporter: HeartbeatReporter, executor) -> object:
    config = SimpleNamespace(
        max_concurrent_tasks=INITIAL_CAPACITY,
        task_memory_limit_mb=STARTUP_MEMORY_LIMIT_MB,
        task_cpu_time_limit_sec=STARTUP_CPU_LIMIT_SEC,
        auto_resource_limit=False,
    )
    return create_engine(
        config,
        transport=MagicMock(),
        runtime_manager=MagicMock(),
        executor=executor,
        plugin_registry=MagicMock(),
        log_manager=MagicMock(),
        project_fetcher=MagicMock(),
        artifact_manager=MagicMock(),
        metrics_collector=collector,
        heartbeat_reporter=reporter,
    )


def _reporter(collector: SystemMetricsCollector) -> HeartbeatReporter:
    return HeartbeatReporter(
        transport=object(),
        worker_id="worker-1",
        metrics_collector=collector,
        max_concurrent_tasks=INITIAL_CAPACITY,
    )


@pytest.mark.asyncio
async def test_heartbeat_reports_engine_effective_task_limits() -> None:
    """生效单任务限额必须上到心跳里, 否则控制面只能显示"未知"。"""
    collector = SystemMetricsCollector(max_slots=INITIAL_CAPACITY)
    reporter = _reporter(collector)
    _wire(collector, reporter, MagicMock(resize_concurrency=AsyncMock()))

    metrics = await reporter._get_metrics()

    assert metrics.task_memory_limit_mb == STARTUP_MEMORY_LIMIT_MB
    assert metrics.task_cpu_time_limit_sec == STARTUP_CPU_LIMIT_SEC


@pytest.mark.asyncio
async def test_effective_task_limits_track_runtime_config_update() -> None:
    """上报的必须是引擎的**活值**, 不是启动时抄下来的一份快照。

    ``apply_config_update`` 只改引擎的 ResourcePolicy, 从不回写 config。谁要是
    从 config 读这两项, 运行时改过限额之后就会一直报旧数字——那正是这个页面
    原本的缺陷形态: 报一个和执行面无关的数。
    """
    collector = SystemMetricsCollector(max_slots=INITIAL_CAPACITY)
    reporter = _reporter(collector)
    engine = _wire(collector, reporter, MagicMock(resize_concurrency=AsyncMock()))

    await engine.apply_config_update({"task_memory_limit_mb": RESIZED_MEMORY_LIMIT_MB})

    assert (await reporter._get_metrics()).task_memory_limit_mb == RESIZED_MEMORY_LIMIT_MB


@pytest.mark.asyncio
async def test_config_update_synchronizes_engine_executor_and_heartbeat() -> None:
    config = SimpleNamespace(
        max_concurrent_tasks=INITIAL_CAPACITY,
        task_memory_limit_mb=512,
        task_cpu_time_limit_sec=60,
        auto_resource_limit=False,
    )
    executor = MagicMock(resize_concurrency=AsyncMock())
    collector = SystemMetricsCollector(max_slots=INITIAL_CAPACITY)
    reporter = HeartbeatReporter(
        transport=object(),
        worker_id="worker-1",
        metrics_collector=collector,
        max_concurrent_tasks=INITIAL_CAPACITY,
    )
    engine = create_engine(
        config,
        transport=MagicMock(),
        runtime_manager=MagicMock(),
        executor=executor,
        plugin_registry=MagicMock(),
        log_manager=MagicMock(),
        project_fetcher=MagicMock(),
        artifact_manager=MagicMock(),
        metrics_collector=collector,
        heartbeat_reporter=reporter,
    )

    await engine.apply_config_update({"max_concurrent_tasks": EXPANDED_CAPACITY})

    assert engine.get_stats()["max_concurrent"] == EXPANDED_CAPACITY
    executor.resize_concurrency.assert_awaited_once_with(EXPANDED_CAPACITY)
    assert (await collector.collect(use_cache=False)).worker.max_slots == EXPANDED_CAPACITY
    assert (await reporter._get_metrics()).max_concurrent_tasks == EXPANDED_CAPACITY
