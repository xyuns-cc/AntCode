"""Run cancellation state transitions outside the main Engine module."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from loguru import logger

from antcode_worker.domain.errors import CancellationError
from antcode_worker.engine.ownership_fence import cancel_executor_run
from antcode_worker.engine.state import CancelRequest, RunState


async def cancel_queued_run(engine: Any, request: CancelRequest, reason: str) -> bool:
    removed = await engine._scheduler.remove(request.run_id)
    if not removed:
        logger.info("取消与 dequeue 并发，交由执行协程读取 cancel_requested: run_id={}", request.run_id)
        return True
    if not await engine._state_manager.transition(request.run_id, RunState.CANCELLED):
        raise CancellationError(
            f"队列任务无法进入 CANCELLED 状态: run_id={request.run_id}",
            run_id=request.run_id,
            reason=reason,
        )
    await engine._report_result_by_info(
        run_id=request.run_id,
        task_id=request.task_id,
        receipt=request.receipt,
        result=engine._build_cancelled_result(
            request.run_id,
            request.queued_at or datetime.now(),
            reason,
        ),
    )
    return True


async def cancel_started_run(engine: Any, request: CancelRequest, reason: str) -> None:
    if request.state == RunState.PREPARING:
        await engine._preparation_tasks.cancel(request.run_id)
        return
    if request.state == RunState.RUNNING:
        await _transition_to_cancelling(engine, request, reason)
    await cancel_executor_run(engine._executor, request.run_id, reason)


async def _transition_to_cancelling(engine: Any, request: CancelRequest, reason: str) -> None:
    if await engine._state_manager.transition(request.run_id, RunState.CANCELLING):
        return
    refreshed = await engine._state_manager.get(request.run_id)
    if refreshed is not None and refreshed.state in (RunState.CANCELLING, RunState.CANCELLED):
        return
    raise CancellationError(
        f"运行任务无法进入 CANCELLING 状态: run_id={request.run_id}",
        run_id=request.run_id,
        reason=reason,
    )


__all__ = ["cancel_queued_run", "cancel_started_run"]
