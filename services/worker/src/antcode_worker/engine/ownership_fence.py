"""Worker run ownership 自隔离与执行器取消边界。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from antcode_worker.domain.errors import CancellationError
from antcode_worker.engine.state import RunState
from antcode_worker.transport.base import GenerationLostError


class OwnershipFenceError(RuntimeError):
    """Run ownership 丢失后触发的 Worker 自隔离错误。"""


class FatalErrorSignal:
    """向应用主循环发布首个 fatal error。"""

    def __init__(self) -> None:
        self._error: BaseException | None = None
        self._event = asyncio.Event()

    def reset(self) -> None:
        self._error = None
        self._event.clear()

    def record(self, error: BaseException) -> None:
        if self._error is None:
            self._error = error
            self._event.set()

    def update(self, error: BaseException) -> None:
        self._error = error
        self._event.set()

    async def wait(self) -> BaseException:
        await self._event.wait()
        if self._error is None:
            raise RuntimeError("fatal error event 已触发但缺少错误")
        return self._error


async def run_with_generation_fence(
    engine: Any,
    operation: Callable[[], Awaitable[Any]],
) -> Any:
    """Turn a propagated generation loss into the engine's process-level fence."""
    try:
        return await operation()
    except GenerationLostError as exc:
        await engine._abort_for_ownership_failure(str(exc))


async def cancel_executor_run(executor: Any, run_id: str, reason: str) -> None:
    """区分任务不存在与真实 kill 失败。"""
    if executor is None:
        return
    task_present = executor.has_task(run_id)
    try:
        cancelled = await executor.cancel(run_id)
    except Exception as exc:
        raise CancellationError(
            f"executor 取消失败: run_id={run_id}",
            run_id=run_id,
            reason=reason,
        ) from exc
    if cancelled:
        return
    if task_present or executor.has_task(run_id):
        raise CancellationError(
            f"executor 未能终止仍在运行的任务: run_id={run_id}",
            run_id=run_id,
            reason=reason,
        )
    logger.warning("executor 未找到 run(已自然结束): run_id={}", run_id)


async def abort_for_ownership_failure(engine: Any, reason: str) -> None:
    """停止 intake/执行并向应用发布不可恢复错误。"""
    engine._polling = False
    engine._running = False
    engine._ownership_fenced = True
    signal = engine._fatal_error_signal
    signal.record(OwnershipFenceError(f"Worker ownership self-fence: {reason}"))
    _cancel_intake_tasks(engine)
    runtime_errors = await _cancel_tasks(engine._inflight_controls)
    cancellation_errors = await _cancel_active_runs(engine, reason)
    shrink_errors = await _cancel_tasks(engine._worker_shrink_tasks)
    worker_errors = await _cancel_tasks({*engine._worker_tasks, *engine._draining_worker_tasks})
    scheduler_error = await _stop_scheduler(engine._scheduler)
    failures = [*runtime_errors, *cancellation_errors, *shrink_errors, *worker_errors]
    if scheduler_error is not None:
        failures.append(scheduler_error)
    fatal = _build_fence_error(reason, failures)
    signal.update(fatal)
    raise fatal


def _cancel_intake_tasks(engine: Any) -> None:
    current = asyncio.current_task()
    for task in (engine._poll_task, engine._control_task, engine._ownership_renewal_task):
        if task is not None and task is not current and not task.done():
            task.cancel()


async def _cancel_tasks(candidates: Any) -> list[BaseException]:
    current = asyncio.current_task()
    tasks = [task for task in candidates if task is not current and not task.done()]
    for task in tasks:
        task.cancel()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return [result for result in results if isinstance(result, BaseException)]


async def _cancel_active_runs(engine: Any, reason: str) -> list[BaseException]:
    active_states = {RunState.RUNNING, RunState.CANCELLING, RunState.PREPARING}
    failures: list[BaseException] = []
    for info in await engine._state_manager.get_all():
        if info.state not in active_states:
            continue
        try:
            await cancel_executor_run(engine._executor, info.run_id, reason)
        except Exception as exc:
            logger.opt(exception=exc).critical("ownership self-fence 终止 {} 失败", info.run_id)
            failures.append(exc)
    return failures


async def _stop_scheduler(scheduler: Any) -> BaseException | None:
    try:
        await scheduler.stop()
    except Exception as exc:
        logger.opt(exception=exc).critical("ownership self-fence 停止调度器失败")
        return exc
    return None


def _build_fence_error(reason: str, failures: list[BaseException]) -> OwnershipFenceError:
    details = [str(error) for error in failures if not isinstance(error, asyncio.CancelledError)]
    suffix = f"; cancellation failures: {' | '.join(details)}" if details else ""
    return OwnershipFenceError(f"Worker ownership self-fence: {reason}{suffix}")
