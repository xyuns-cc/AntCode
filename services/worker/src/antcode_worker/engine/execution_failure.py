"""Task execution failure classification outside the main engine loop."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from loguru import logger

from antcode_worker.domain.enums import ExitReason, RunStatus
from antcode_worker.domain.models import ExecResult
from antcode_worker.engine.preparation_tasks import PreparationCancelledError
from antcode_worker.engine.state import RunState
from antcode_worker.transport.generation import raise_if_generation_lost


async def handle_execution_failure(
    *,
    engine: Any,
    run_id: str,
    started_at: datetime,
    error: Exception,
) -> ExecResult:
    raise_if_generation_lost(error)
    if isinstance(error, PreparationCancelledError):
        await engine._state_manager.transition(run_id, RunState.CANCELLED)
        return ExecResult(
            run_id=run_id,
            status=RunStatus.CANCELLED,
            exit_reason=ExitReason.CANCELLED,
            error_message="任务准备阶段已取消",
            started_at=started_at,
            finished_at=datetime.now(),
        )
    logger.opt(exception=error).error("执行失败: {}", run_id)
    await engine._state_manager.transition(run_id, RunState.FAILED)
    return ExecResult(
        run_id=run_id,
        status=RunStatus.FAILED,
        exit_reason=ExitReason.ERROR,
        error_message=str(error),
        started_at=started_at,
        finished_at=datetime.now(),
    )
