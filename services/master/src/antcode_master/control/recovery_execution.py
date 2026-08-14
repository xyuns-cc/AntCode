"""Transactional source-run transition for deterministic recovery runs."""

from datetime import UTC, datetime
from typing import Any

from antcode_core.domain.models.enums import RuntimeStatus, TaskStatus
from antcode_core.domain.models.task_run import TaskRun

from antcode_master.control.execution_parameters import RecoveryExecutionOptions


class RecoverySourceInvalidError(RuntimeError):
    pass


async def lock_interrupted_source(
    connection: Any,
    task_id: int,
    options: RecoveryExecutionOptions,
) -> Any:
    source = await TaskRun.filter(run_id=options.source_run_id).using_db(connection).select_for_update().first()
    if source is None or source.task_id != task_id or source.status != TaskStatus.RUNNING:
        raise RecoverySourceInvalidError(f"恢复源 run 已失效: run_id={options.source_run_id}")
    return source


async def transition_interrupted_source(connection: Any, source: Any) -> None:
    now = datetime.now(UTC)
    updated = (
        await TaskRun.filter(id=source.id)
        .using_db(connection)
        .update(
            status=TaskStatus.FAILED,
            runtime_status=RuntimeStatus.FAILED,
            runtime_updated_at=now,
            end_time=now,
            error_message="任务中断，已重新调度",
        )
    )
    if updated != 1:
        raise RecoverySourceInvalidError(f"恢复源 run 终结失败: run_id={source.run_id}")


__all__ = [
    "RecoverySourceInvalidError",
    "lock_interrupted_source",
    "transition_interrupted_source",
]
