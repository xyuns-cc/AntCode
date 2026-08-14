"""Ordered Engine shutdown with observable task completion."""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger


async def stop_engine(engine: Any, grace_period: float) -> None:
    """Stop intake, settle active work, then await every Engine-owned task."""
    if not engine._running and not _owned_tasks(engine):
        return
    logger.info("开始停止引擎...")
    failures: list[Exception] = []
    engine._polling = False
    failures.extend(await _cancel_and_wait(_pre_drain_tasks(engine)))
    deadline = asyncio.get_running_loop().time() + max(0.0, grace_period)
    if not engine._ownership_fenced:
        try:
            await _drain_active_runs(engine, deadline)
            await engine._drain_runtime_controls(_remaining(deadline))
        except Exception as exc:
            logger.opt(exception=exc).error("Engine 任务 drain 失败")
            failures.append(exc)
    failures.extend(await _cancel_and_wait(_ownership_tasks(engine)))
    engine._running = False
    failures.extend(await _cancel_and_wait(_worker_tasks(engine)))
    engine._worker_tasks.clear()
    engine._draining_worker_tasks.clear()
    engine._worker_shrink_tasks.clear()
    engine._worker_run_ids.clear()
    engine._poll_task = None
    engine._control_task = None
    engine._ownership_renewal_task = None
    try:
        await engine._scheduler.stop()
    except Exception as exc:
        logger.opt(exception=exc).error("Engine scheduler 停止失败")
        failures.append(exc)
    if failures:
        raise ExceptionGroup("Engine 停止阶段失败", failures)
    logger.info("引擎已停止")


def _pre_drain_tasks(engine: Any) -> set[asyncio.Task]:
    tasks = {task for task in (engine._poll_task, engine._control_task) if task is not None}
    tasks.update(getattr(engine, "_worker_shrink_tasks", set()))
    return tasks


def _ownership_tasks(engine: Any) -> set[asyncio.Task]:
    renewal = engine._ownership_renewal_task
    return {renewal} if renewal is not None else set()


def _worker_tasks(engine: Any) -> set[asyncio.Task]:
    return {*engine._worker_tasks, *engine._draining_worker_tasks}


def _owned_tasks(engine: Any) -> set[asyncio.Task]:
    return _pre_drain_tasks(engine) | _ownership_tasks(engine) | _worker_tasks(engine)


async def _cancel_and_wait(tasks: set[asyncio.Task]) -> list[Exception]:
    current = asyncio.current_task()
    owned = {task for task in tasks if task is not current}
    for task in owned:
        if not task.done():
            task.cancel()
    results = await asyncio.gather(*owned, return_exceptions=True)
    return [result for result in results if isinstance(result, Exception)]


async def _drain_active_runs(engine: Any, deadline: float) -> None:
    active_count = await engine._state_manager.count_active()
    if active_count == 0:
        return
    timeout = _remaining(deadline)
    logger.info(f"等待 {active_count} 个任务完成 (最长 {timeout:.1f}s)...")
    try:
        await asyncio.wait_for(engine._drain_tasks(), timeout=timeout)
    except TimeoutError:
        logger.warning("等待超时，强制终止任务")
        await engine._force_terminate()


def _remaining(deadline: float) -> float:
    return max(0.0, deadline - asyncio.get_running_loop().time())


__all__ = ["stop_engine"]
