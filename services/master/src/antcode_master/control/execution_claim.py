"""Transactional TaskRun claim state machine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from antcode_core.domain.models.enums import DispatchStatus, TaskStatus
from loguru import logger

from antcode_master.control.execution_parameters import add_recovery_context
from antcode_master.control.retry_intent_guard import RetryTargetInvalidError


@dataclass(frozen=True)
class ExecutionClaimRequest:
    task_id: int
    run_id: str
    retry_options: Any = None
    recovery_options: Any = None
    scheduler_fencing_token: int | None = None


@dataclass(frozen=True)
class ExecutionClaimDependencies:
    task_model: Any
    task_run_model: Any
    transaction_factory: Any
    require_fencing_token: Any
    require_scheduler_authority: Any
    lock_interrupted_source: Any
    transition_interrupted_source: Any


async def claim_task_run(service: Any, request: ExecutionClaimRequest, dependencies: ExecutionClaimDependencies):
    async with dependencies.transaction_factory("default") as connection:
        token = await _scheduler_token(request, dependencies)
        await dependencies.require_scheduler_authority(connection, token)
        task = await _lock_task(dependencies.task_model, connection, request.task_id)
        if task is None:
            return _missing_task_outcome(request)
        recovery_source = await _validate_sources(
            service,
            request=request,
            dependencies=dependencies,
            connection=connection,
        )
        if not _target_is_active(task, request):
            return None
        if await _at_capacity(service, connection=connection, task=task, recovery_source=recovery_source):
            return None
        if not await _queue_task(
            dependencies.task_model,
            connection=connection,
            task=task,
            recovery_source=recovery_source,
        ):
            return None
        if recovery_source is not None:
            await dependencies.transition_interrupted_source(connection, recovery_source)
        result_data, retry_count = await _run_metadata(service, connection=connection, request=request)
        execution = await dependencies.task_run_model.create(
            run_id=request.run_id,
            task_id=task.id,
            status=TaskStatus.QUEUED,
            dispatch_status=DispatchStatus.PENDING,
            runtime_status=None,
            start_time=None,
            retry_count=retry_count,
            result_data=result_data or None,
            scheduler_fencing_token=token,
            using_db=connection,
        )
        task.status = TaskStatus.QUEUED
        return task, execution


async def _scheduler_token(request: ExecutionClaimRequest, dependencies: ExecutionClaimDependencies) -> int:
    if request.scheduler_fencing_token is not None:
        return request.scheduler_fencing_token
    return await dependencies.require_fencing_token()


async def _lock_task(task_model: Any, connection: Any, task_id: int):
    return await task_model.filter(id=task_id).using_db(connection).select_for_update().first()


def _missing_task_outcome(request: ExecutionClaimRequest):
    if request.retry_options is not None:
        raise RetryTargetInvalidError(f"retry target task 不存在: task_id={request.task_id}")
    return None


async def _validate_sources(service, *, request, dependencies, connection):
    if request.retry_options is not None:
        await service._validate_retry_source(connection, request.task_id, request.retry_options)
    if request.recovery_options is None:
        return None
    if request.retry_options is not None:
        raise ValueError("retry run 不能同时携带 recovery context")
    return await dependencies.lock_interrupted_source(connection, request.task_id, request.recovery_options)


def _target_is_active(task: Any, request: ExecutionClaimRequest) -> bool:
    if task.is_active:
        return True
    logger.warning(f"任务 {task.name} 未激活，跳过执行")
    if request.retry_options is not None:
        raise RetryTargetInvalidError(f"retry target task 未激活: task_id={request.task_id}")
    return False


async def _at_capacity(service: Any, *, connection: Any, task: Any, recovery_source: Any) -> bool:
    allowed = service._task_max_instances(task)
    active_runs = await service._count_active_runs(connection, task.id)
    if recovery_source is not None:
        active_runs = max(0, active_runs - 1)
    if active_runs < allowed:
        return False
    logger.warning(f"任务 {task.name} 活跃实例已达上限 ({active_runs}/{allowed})，跳过重复触发")
    return True


async def _queue_task(task_model: Any, *, connection: Any, task: Any, recovery_source: Any) -> bool:
    updated = await task_model.filter(id=task.id).using_db(connection).update(status=TaskStatus.QUEUED)
    if updated == 1:
        return True
    if recovery_source is not None:
        raise RuntimeError(f"恢复 run 创建前 Task 状态更新失败: task_id={task.id}")
    return False


async def _run_metadata(service: Any, *, connection: Any, request: ExecutionClaimRequest) -> tuple[dict, int]:
    result_data: dict = {}
    retry_count = 0
    if request.retry_options is not None:
        retry_count = request.retry_options.retry_count
        result_data["retry_source_run_id"] = request.retry_options.source_run_id
        await service._consume_retry_intent(connection, request.retry_options)
    return add_recovery_context(result_data, request.recovery_options), retry_count


__all__ = ["ExecutionClaimDependencies", "ExecutionClaimRequest", "claim_task_run"]
