"""Successful executor-return processing for Worker runs."""

from __future__ import annotations

from typing import Any

from antcode_worker.domain.enums import RunStatus
from antcode_worker.domain.models import ExecPlan, ExecResult, RunContext
from antcode_worker.engine.state import RunState


async def complete_execution(
    engine: Any,
    exec_plan: ExecPlan,
    exec_result: ExecResult,
    *,
    context: RunContext,
    runtime_handle: Any,
    log_manager: Any,
) -> ExecResult:
    """Collect artifacts, flush logs, and set terminal state."""
    await _collect_artifacts(
        engine,
        exec_plan,
        exec_result,
        runtime_handle=runtime_handle,
        run_id=context.run_id,
    )
    if log_manager:
        await log_manager.flush()
    await _transition_terminal_state(engine, context.run_id, exec_result.status)
    return exec_result


async def _collect_artifacts(
    engine: Any,
    exec_plan: ExecPlan,
    exec_result: ExecResult,
    *,
    runtime_handle: Any,
    run_id: str,
) -> None:
    manager = engine._artifact_manager
    if not manager or not exec_plan.artifact_patterns:
        return
    collection = await manager.collect_artifacts(
        work_dir=exec_plan.cwd or runtime_handle.path,
        patterns=exec_plan.artifact_patterns,
        run_id=run_id,
    )
    for artifact in collection.artifacts:
        exec_result.artifacts.append(await manager.store_artifact(artifact, run_id))


async def _transition_terminal_state(engine: Any, run_id: str, status: RunStatus) -> None:
    if status == RunStatus.SUCCESS:
        await engine._state_manager.transition(run_id, RunState.COMPLETED)
        return
    if status != RunStatus.CANCELLED:
        await engine._state_manager.transition(run_id, RunState.FAILED)
        return
    info = await engine._state_manager.get(run_id)
    if info and info.state != RunState.CANCELLED:
        await engine._state_manager.transition(run_id, RunState.CANCELLED)


__all__ = ["complete_execution"]
