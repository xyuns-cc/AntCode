"""Sandbox execution cancellation helpers."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from antcode_worker.domain.enums import ExitReason, RunStatus
from antcode_worker.domain.models import ExecPlan, ExecResult


@dataclass
class SandboxRunMarker:
    cancel_requested: bool = False


def cancelled_before_process_start(
    executor: Any,
    run_id: str,
    started_at: datetime,
) -> ExecResult:
    return executor._create_result(
        run_id=run_id,
        status=RunStatus.CANCELLED,
        exit_reason=ExitReason.CANCELLED,
        error_message="任务在沙箱准备阶段被取消",
        started_at=started_at,
        finished_at=datetime.now(),
    )


async def cancel_pending_process_task(process_task: asyncio.Task[ExecResult] | None) -> None:
    if process_task is None or process_task.done():
        return
    process_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await process_task


def payload_process_limit(exec_plan: ExecPlan, config: Any) -> int:
    enforce = exec_plan.enforce_rlimit if exec_plan.enforce_rlimit is not None else config.enforce_rlimit
    if not enforce:
        return 0
    return exec_plan.max_processes or config.default_max_processes


__all__ = [
    "SandboxRunMarker",
    "cancel_pending_process_task",
    "cancelled_before_process_start",
    "payload_process_limit",
]
