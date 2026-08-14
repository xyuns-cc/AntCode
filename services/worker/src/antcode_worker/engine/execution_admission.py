"""Cancellation-safe admission for executor runs."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from antcode_worker.domain.models import ExecPlan, ExecResult
from antcode_worker.engine.ownership_fence import cancel_executor_run
from antcode_worker.engine.rule_egress import rule_egress_plan
from antcode_worker.executor.concurrency import ExecutionAdmission


@contextlib.contextmanager
def execution_egress(engine: Any, plan: ExecPlan):
    config = engine._executor.config
    with rule_egress_plan(
        plan, config.rule_egress_limits, default_max_processes=config.default_max_processes
    ) as secured:
        yield secured


async def execute_with_admission(
    engine: Any,
    execution_plan: ExecPlan,
    *,
    runtime_handle: Any,
    log_sink: Any,
) -> ExecResult:
    """Do not release executor startup until cancellation can reach the run."""
    run_id = execution_plan.run_id
    if not run_id:
        raise RuntimeError("执行计划缺少 run_id")
    admission = ExecutionAdmission()
    execution = asyncio.create_task(
        engine._executor.run(
            execution_plan,
            runtime_handle,
            log_sink=log_sink,
            admission=admission,
        )
    )
    try:
        registered = await admission.wait_until_registered_or_done(execution)
        if not registered:
            return await execution
        if await engine._is_cancel_requested(run_id):
            await cancel_executor_run(
                engine._executor,
                run_id,
                "cancelled during executor admission",
            )
        admission.release()
        return await execution
    finally:
        admission.release()
        if not execution.done():
            execution.cancel()
        await asyncio.gather(execution, return_exceptions=True)


__all__ = ["execute_with_admission"]
