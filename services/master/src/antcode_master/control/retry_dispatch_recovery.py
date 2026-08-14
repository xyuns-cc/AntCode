"""Crash recovery helpers for deterministic retry runs."""

from __future__ import annotations

from datetime import UTC, datetime

from antcode_core.application.services.lease_fenced_ready_publish import (
    SchedulerDispatchFenceError,
)
from antcode_core.application.services.projects.relation_service import relation_service
from antcode_core.application.services.scheduler.execution_status_service import (
    execution_status_service,
)
from antcode_core.domain.models.enums import DispatchStatus, TaskStatus
from antcode_core.domain.models.task_run import TaskRun
from antcode_core.observability.tracing import new_trace, set_current_trace
from loguru import logger

from antcode_master.control.retry_intent_delivery import RetryIntentDeliveryError
from antcode_master.control.retry_intent_guard import (
    RetryIntent,
    RetryIntentInvalidError,
    RetryTargetInvalidError,
)
from antcode_master.control.scheduler_authority import (
    SchedulerAuthorityLost,
    execute_with_scheduler_authority,
    reset_unpublished_dispatch,
    take_over_pre_dispatch_run,
)
from antcode_master.control.scheduler_failure_wiring import (
    record_dispatch_outcome,
    scheduler_epoch,
    settle_unexpected_failure,
)
from antcode_master.leader import require_fencing_token

ACTIVE_RUN_STATUSES = (
    TaskStatus.PENDING,
    TaskStatus.QUEUED,
    TaskStatus.DISPATCHING,
    TaskStatus.RUNNING,
)
RECOVERABLE_RETRY_RUN_STATUSES = (TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.DISPATCHING)


def validate_existing_retry_run(execution, intent: RetryIntent) -> None:
    result_data = execution.result_data or {}
    valid = (
        execution.task_id == intent.task_id
        and execution.retry_count == intent.retry_count
        and result_data.get("retry_source_run_id") == intent.source_run_id
    )
    if not valid:
        raise RetryIntentInvalidError(f"existing retry run 与 intent 不匹配: run_id={execution.run_id}")


def is_recoverable_retry_run(execution) -> bool:
    return (
        execution.dispatch_status in (DispatchStatus.PENDING, DispatchStatus.DISPATCHING)
        and execution.runtime_status is None
        and execution.status in RECOVERABLE_RETRY_RUN_STATUSES
    )


def task_max_instances(task) -> int:
    try:
        value = int(getattr(task, "max_instances", 1) or 1)
    except (TypeError, ValueError):
        value = 1
    return max(1, value)


async def resume_retry_run(service, intent: RetryIntent, execution) -> str:
    """Resume a retry run committed before its first dispatch CAS."""
    token = await require_fencing_token()
    execution = await take_over_pre_dispatch_run(execution.run_id, token)
    if execution is None:
        raise SchedulerAuthorityLost(f"retry run 无法由当前任期接管: run_id={intent.source_run_id}")
    task_info = await relation_service.get_task_with_project(intent.task_id)
    if not task_info:
        await _fail_unrecoverable_retry_run(execution, token, "目标任务不存在")
        raise RetryTargetInvalidError(f"retry target task 不存在: task_id={intent.task_id}")
    task = task_info["task"]
    if not task.is_active:
        await _fail_unrecoverable_retry_run(execution, token, "目标任务未激活")
        raise RetryTargetInvalidError(f"retry target task 未激活: task_id={intent.task_id}")
    trace_ids = new_trace()
    set_current_trace(trace_ids.traceparent)
    prepared = _prepare_existing_execution(task_info, execution)
    with service._track_execution():
        return await service._run_prepared_execution(intent.task_id, prepared)


async def resume_existing_run(service, task_id: int, execution) -> str:
    """Resume a deterministic non-retry run committed before dispatch."""
    task_info = await relation_service.get_task_with_project(task_id)
    if not task_info or not task_info["task"].is_active:
        raise RuntimeError(f"deterministic run target is missing or inactive: task_id={task_id}")
    trace_ids = new_trace()
    set_current_trace(trace_ids.traceparent)
    prepared = _prepare_existing_execution(task_info, execution)
    with service._track_execution():
        return await service._run_prepared_execution(task_id, prepared)


async def _fail_unrecoverable_retry_run(execution, token: int, reason: str) -> None:
    updated = await execute_with_scheduler_authority(
        token,
        execution_status_service.update_dispatch_status,
        run_id=execution.run_id,
        status=DispatchStatus.FAILED,
        status_at=datetime.now(UTC),
        error_message=f"retry run 无法恢复派发: {reason}",
    )
    if not updated:
        raise RuntimeError(f"无法终结不可恢复的 retry run: run_id={execution.run_id}")


def _prepare_existing_execution(task_info, execution):
    task = task_info["task"]
    now = datetime.now(UTC)
    return task, task_info["project"], task_info["project_detail"], execution, now


async def run_prepared_execution(service, task_id, prepared) -> str:
    """Dispatch a newly-created or crash-recovered run."""
    task, project, project_detail, execution, now = prepared
    run_id = execution.run_id
    result_success = None
    distributed_pending = False
    try:
        await execute_with_scheduler_authority(
            scheduler_epoch(execution),
            _announce_execution_start,
            service=service,
            task=task,
            execution=execution,
        )
        result = await service._dispatch_and_run(
            task,
            project,
            project_detail,
            execution,
            run_id,
            now,
        )
        if result:
            result_success, distributed_pending = await record_dispatch_outcome(
                service,
                execution=execution,
                result=result,
            )
    except TimeoutError:
        result_success = await settle_unexpected_failure(
            service,
            execution,
            message="任务执行超时",
            terminal_status=DispatchStatus.TIMEOUT,
        )
    except SchedulerAuthorityLost:
        task = None
        raise
    except SchedulerDispatchFenceError as exc:
        await reset_unpublished_dispatch(run_id, int(execution.scheduler_fencing_token))
        task = None
        raise SchedulerAuthorityLost(f"派发前 Master 任期已切换: run_id={run_id}") from exc
    except RetryIntentDeliveryError:
        task = None
        raise
    except Exception as exc:
        logger.exception(f"执行任务失败: task_id={task_id} run_id={run_id}")
        result_success = await settle_unexpected_failure(
            service,
            execution,
            message=str(exc),
            terminal_status=DispatchStatus.FAILED,
        )
    finally:
        await _finalize_execution(
            service,
            (task, task_id, execution, result_success, distributed_pending),
        )
    return run_id


async def _announce_execution_start(service, *, task, execution) -> None:
    """只写任务日志：调度面不发实时 run_status 帧（见 scheduler_loop 模块 docstring）。"""
    await service._log_execution(execution, "INFO", f"开始执行任务: {task.name}")


async def _finalize_execution(service, context) -> None:
    task, task_id, execution, result_success, distributed_pending = context
    kwargs = {
        "task": task,
        "task_id": task_id,
        "result_success": result_success,
        "distributed_pending": distributed_pending,
    }
    if task is None:
        await service._finalize_stats(**kwargs)
        return
    try:
        await execute_with_scheduler_authority(
            scheduler_epoch(execution),
            service._finalize_stats,
            **kwargs,
        )
    except SchedulerAuthorityLost:
        await service._finalize_stats(**{**kwargs, "task": None})
        raise


async def count_active_runs(conn, task_id: int) -> int:
    return await TaskRun.filter(task_id=task_id, status__in=list(ACTIVE_RUN_STATUSES)).using_db(conn).count()


__all__ = [
    "count_active_runs",
    "is_recoverable_retry_run",
    "resume_retry_run",
    "resume_existing_run",
    "run_prepared_execution",
    "task_max_instances",
    "validate_existing_retry_run",
]
