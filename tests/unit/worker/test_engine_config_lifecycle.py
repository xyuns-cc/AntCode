"""Regressions for live Engine configuration and worker resizing."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from antcode_worker.engine.engine import Engine
from antcode_worker.transport.base import ControlMessage, GenerationLostError

EXPECTED_MEMORY_LIMIT_MB = 512
EXPECTED_CPU_LIMIT_SECONDS = 60
INITIAL_CONCURRENCY = 2
ADAPTIVE_CONCURRENCY = 3
ADAPTIVE_MEMORY_LIMIT_MB = 1024
ADAPTIVE_CPU_LIMIT_SECONDS = 300
EXPLICIT_CONCURRENCY = 4
EXPLICIT_ADAPTIVE_MEMORY_LIMIT_MB = 2048
EXPLICIT_CPU_LIMIT_SECONDS = 600


def _engine(*, max_concurrent: int = 2) -> Engine:
    transport = MagicMock(is_connected=True)
    transport._worker_id = "worker-test"
    transport._lease_id = "lease-test"
    transport.ack_control = AsyncMock(return_value=True)
    executor = MagicMock()
    executor.has_task.return_value = False
    executor.cancel = AsyncMock(return_value=False)
    executor.resize_concurrency = AsyncMock()
    return Engine(transport=transport, executor=executor, max_concurrent=max_concurrent)


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"auto_resource_limit": 1},
        {"max_concurrent_tasks": True},
        {"max_concurrent_tasks": 21},
        {"max_concurrent_tasks": 1.5},
        {"task_memory_limit_mb": 255},
        {"task_cpu_time_limit_sec": 59},
        {"unknown": 1},
    ],
)
@pytest.mark.asyncio
async def test_config_update_rejects_invalid_payload_atomically(config: dict) -> None:
    engine = _engine()
    resize = AsyncMock()
    engine._resize_workers = resize  # type: ignore[method-assign]
    before = (
        engine._policies.resource.memory_limit_mb,
        engine._policies.resource.cpu_limit_seconds,
    )

    with pytest.raises(ValueError):
        await engine.apply_config_update(config)

    resize.assert_not_awaited()
    assert before == (
        engine._policies.resource.memory_limit_mb,
        engine._policies.resource.cpu_limit_seconds,
    )


@pytest.mark.asyncio
async def test_config_update_accepts_bounded_string_values() -> None:
    engine = _engine()
    engine._resize_workers = AsyncMock()  # type: ignore[method-assign]

    await engine.apply_config_update(
        {
            "max_concurrent_tasks": "3",
            "task_memory_limit_mb": "512",
            "task_cpu_time_limit_sec": "60",
            "auto_resource_limit": "false",
        }
    )

    engine._resize_workers.assert_awaited_once_with(3)  # type: ignore[attr-defined]
    assert engine._policies.resource.memory_limit_mb == EXPECTED_MEMORY_LIMIT_MB
    assert engine._policies.resource.cpu_limit_seconds == EXPECTED_CPU_LIMIT_SECONDS
    assert engine._auto_resource_limit is False


@pytest.mark.asyncio
async def test_enabling_auto_mode_applies_all_adaptive_values() -> None:
    provider = MagicMock(
        return_value={
            "max_concurrent_tasks": ADAPTIVE_CONCURRENCY,
            "task_memory_limit_mb": ADAPTIVE_MEMORY_LIMIT_MB,
            "task_cpu_time_limit_sec": ADAPTIVE_CPU_LIMIT_SECONDS,
        }
    )
    observer = MagicMock()
    engine = _engine()
    engine._adaptive_limits_provider = provider
    engine._capacity_observer = observer

    await engine.apply_config_update({"auto_resource_limit": "true"})

    provider.assert_called_once_with()
    engine._executor.resize_concurrency.assert_awaited_once_with(ADAPTIVE_CONCURRENCY)
    observer.assert_called_once_with(ADAPTIVE_CONCURRENCY)
    assert engine._max_concurrent == ADAPTIVE_CONCURRENCY
    assert engine._policies.resource.memory_limit_mb == ADAPTIVE_MEMORY_LIMIT_MB
    assert engine._policies.resource.cpu_limit_seconds == ADAPTIVE_CPU_LIMIT_SECONDS
    assert engine._auto_resource_limit is True


@pytest.mark.asyncio
async def test_capacity_update_rolls_back_scheduler_when_executor_resize_fails() -> None:
    engine = _engine(max_concurrent=INITIAL_CONCURRENCY)
    engine._scheduler.update_max_size = AsyncMock()
    engine._executor.resize_concurrency.side_effect = [RuntimeError("resize failed"), None]

    with pytest.raises(RuntimeError, match="resize failed"):
        await engine.apply_config_update({"max_concurrent_tasks": ADAPTIVE_CONCURRENCY})

    assert engine._scheduler.update_max_size.await_args_list == [
        call(ADAPTIVE_CONCURRENCY * 2),
        call(INITIAL_CONCURRENCY * 2),
    ]
    assert engine._executor.resize_concurrency.await_args_list == [
        call(ADAPTIVE_CONCURRENCY),
        call(INITIAL_CONCURRENCY),
    ]
    assert engine._max_concurrent == INITIAL_CONCURRENCY


@pytest.mark.asyncio
async def test_explicit_value_wins_when_enabling_auto_mode() -> None:
    engine = _engine()
    engine._adaptive_limits_provider = MagicMock(
        return_value={
            "max_concurrent_tasks": EXPLICIT_CONCURRENCY,
            "task_memory_limit_mb": EXPLICIT_ADAPTIVE_MEMORY_LIMIT_MB,
            "task_cpu_time_limit_sec": EXPLICIT_CPU_LIMIT_SECONDS,
        }
    )

    await engine.apply_config_update(
        {
            "auto_resource_limit": True,
            "task_memory_limit_mb": EXPECTED_MEMORY_LIMIT_MB,
        }
    )

    assert engine._max_concurrent == EXPLICIT_CONCURRENCY
    assert engine._policies.resource.memory_limit_mb == EXPECTED_MEMORY_LIMIT_MB
    assert engine._policies.resource.cpu_limit_seconds == EXPLICIT_CPU_LIMIT_SECONDS


@pytest.mark.asyncio
async def test_disabling_auto_mode_alone_keeps_current_numeric_limits() -> None:
    engine = _engine()
    engine._auto_resource_limit = True
    engine._adaptive_limits_provider = MagicMock()
    before = (
        engine._max_concurrent,
        engine._policies.resource.memory_limit_mb,
        engine._policies.resource.cpu_limit_seconds,
    )

    await engine.apply_config_update({"auto_resource_limit": False})

    engine._adaptive_limits_provider.assert_not_called()
    engine._executor.resize_concurrency.assert_not_awaited()
    assert before == (
        engine._max_concurrent,
        engine._policies.resource.memory_limit_mb,
        engine._policies.resource.cpu_limit_seconds,
    )
    assert engine._auto_resource_limit is False


@pytest.mark.asyncio
async def test_config_update_operation_failure_prevents_control_ack() -> None:
    engine = _engine()
    engine._resize_workers = AsyncMock(side_effect=RuntimeError("resize failed"))  # type: ignore[method-assign]
    control = ControlMessage(
        control_type="config_update",
        payload={"max_concurrent_tasks": "3"},
        receipt="control-1",
    )

    with pytest.raises(RuntimeError, match="resize failed"):
        await engine._dispatch_control(control)

    engine._transport.ack_control.assert_not_awaited()


@pytest.mark.asyncio
async def test_dynamic_expansion_uses_generation_fence() -> None:
    engine = _engine(max_concurrent=1)
    engine._running = True
    engine._abort_for_ownership_failure = AsyncMock()  # type: ignore[method-assign]

    async def lose_generation(_worker_id: int) -> None:
        raise GenerationLostError("superseded")

    engine._worker_loop = lose_generation  # type: ignore[method-assign]
    await engine._resize_workers(2)
    await asyncio.gather(*engine._worker_tasks)

    engine._abort_for_ownership_failure.assert_awaited_once_with("superseded")  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_config_shrink_does_not_block_control_loop() -> None:
    engine = _engine(max_concurrent=2)
    engine._running = True
    blockers = [asyncio.create_task(asyncio.Event().wait()) for _ in range(2)]
    engine._worker_tasks = blockers
    control = ControlMessage(
        control_type="config_update",
        payload={"max_concurrent_tasks": "1"},
        receipt="control-1",
    )

    await asyncio.wait_for(engine._dispatch_control(control), timeout=0.1)

    engine._transport.ack_control.assert_awaited_once_with("control-1")
    assert len(engine._worker_tasks) == 1
    assert len(engine._worker_shrink_tasks) == 1
    for task in [*engine._worker_shrink_tasks, *blockers]:
        task.cancel()
    await asyncio.gather(*engine._worker_shrink_tasks, *blockers, return_exceptions=True)


@pytest.mark.asyncio
async def test_background_shrink_failure_is_reported_as_fatal() -> None:
    engine = _engine(max_concurrent=2)
    engine._running = True
    worker = asyncio.create_task(asyncio.Event().wait())
    engine._worker_tasks = [asyncio.create_task(asyncio.Event().wait()), worker]

    async def fail_shrink(_draining: list[asyncio.Task]) -> None:
        raise RuntimeError("drain failed")

    engine._shrink_workers = fail_shrink  # type: ignore[method-assign]
    await engine._resize_workers(1)
    fatal = await asyncio.wait_for(engine.wait_for_fatal_error(), timeout=0.1)

    assert "drain failed" in str(fatal)
    for task in engine._worker_tasks:
        task.cancel()
    worker.cancel()
    await asyncio.gather(*engine._worker_tasks, worker, return_exceptions=True)


@pytest.mark.asyncio
async def test_shrink_cancel_failure_is_reported_after_hard_cleanup() -> None:
    engine = _engine(max_concurrent=2)
    worker = asyncio.create_task(asyncio.Event().wait())
    engine._draining_worker_tasks.add(worker)
    engine._worker_run_ids[worker] = "run-1"
    engine.cancel = AsyncMock(side_effect=RuntimeError("cancel failed"))  # type: ignore[method-assign]
    engine._SHRINK_DRAIN_GRACE_SECONDS = 0
    engine._SHRINK_CANCEL_TIMEOUT_SECONDS = 0

    with pytest.raises(ExceptionGroup, match="worker 缩容取消失败"):
        await engine._shrink_workers([worker])

    assert worker.cancelled()
    assert worker not in engine._draining_worker_tasks
