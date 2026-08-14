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
