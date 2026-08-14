"""Transactional Worker result commit with exact lease fencing."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from loguru import logger
from tortoise.transactions import in_transaction

from antcode_core.application.services.scheduler.execution_status_service import (
    execution_status_service,
)
from antcode_core.common.error_messages import normalize_persisted_error_message
from antcode_core.domain.models.enums import DispatchStatus, RuntimeStatus, TaskStatus
from antcode_core.domain.models.task_run import TaskRun
from antcode_core.domain.models.worker import Worker

LeaseValidator = Callable[[str, str], Awaitable[bool]]
MetadataBuilder = Callable[[TaskRun], dict[str, Any]]
MAX_LEASE_ID_LENGTH = 64
MAX_PUBLIC_ID_LENGTH = 32

_PROGRESS_STATUSES = frozenset({RuntimeStatus.QUEUED, RuntimeStatus.RUNNING})
_RUNTIME_TERMINAL = frozenset(
    {
        RuntimeStatus.SUCCESS,
        RuntimeStatus.FAILED,
        RuntimeStatus.CANCELLED,
        RuntimeStatus.TIMEOUT,
        RuntimeStatus.SKIPPED,
    }
)
_DISPATCH_FAILURE_TERMINAL = frozenset({DispatchStatus.REJECTED, DispatchStatus.TIMEOUT, DispatchStatus.FAILED})
_RUNTIME_ORDER = {
    RuntimeStatus.QUEUED: 1,
    RuntimeStatus.RUNNING: 2,
    RuntimeStatus.SUCCESS: 3,
    RuntimeStatus.FAILED: 3,
    RuntimeStatus.CANCELLED: 3,
    RuntimeStatus.TIMEOUT: 3,
    RuntimeStatus.SKIPPED: 3,
}
_TASK_STATUS_BY_RUNTIME = {
    RuntimeStatus.QUEUED: TaskStatus.QUEUED,
    RuntimeStatus.RUNNING: TaskStatus.RUNNING,
    RuntimeStatus.SUCCESS: TaskStatus.SUCCESS,
    RuntimeStatus.FAILED: TaskStatus.FAILED,
    RuntimeStatus.CANCELLED: TaskStatus.CANCELLED,
    RuntimeStatus.TIMEOUT: TaskStatus.TIMEOUT,
    RuntimeStatus.SKIPPED: TaskStatus.SKIPPED,
}


@dataclass(frozen=True, slots=True)
class ResultCommitRequest:
    run_id: str
    worker_id: str
    lease_id: str
    runtime_status: RuntimeStatus
    status_at: datetime
    exit_code: int | None
    error_message: str | None
    metadata_builder: MetadataBuilder


@dataclass(frozen=True, slots=True)
class ResultCommitOutcome:
    """Authoritative result of the ownership-fenced row transaction."""

    accepted: bool
    run_id: str
    runtime_status: RuntimeStatus | None


@dataclass(frozen=True, slots=True)
class _UpdateDecision:
    accepted: bool
    updates: dict[str, Any]
    runtime_status: RuntimeStatus | None


class ResultMetadataRejected(ValueError):
    """The result metadata cannot be persisted under the server contract."""


class TaskResultCommitter:
    """Commit a result only for the generation already bound by ownership claim."""

    def __init__(self, lease_validator: LeaseValidator | None) -> None:
        self._lease_validator = lease_validator

    async def commit(self, request: ResultCommitRequest) -> bool:
        """Compatibility wrapper for callers that only need acceptance."""
        return (await self.commit_outcome(request)).accepted

    async def commit_outcome(self, request: ResultCommitRequest) -> ResultCommitOutcome:
        if not self._valid_identity(request):
            return _rejected_outcome(request.run_id)
        lease_is_current = await self._is_current(request)
        outcome = await self._commit_locked(request, lease_is_current)
        if not outcome.accepted:
            return outcome
        execution = await TaskRun.get_or_none(run_id=outcome.run_id)
        if execution is not None:
            await execution_status_service._sync_task_status(execution, request.status_at)
        return outcome

    def _valid_identity(self, request: ResultCommitRequest) -> bool:
        if not request.worker_id or request.worker_id != request.worker_id.strip():
            logger.warning("结果缺少规范 Worker 身份: run_id={}", request.run_id)
            return False
        if (
            not request.lease_id
            or request.lease_id != request.lease_id.strip()
            or len(request.lease_id) > MAX_LEASE_ID_LENGTH
        ):
            logger.warning("结果 lease_id 缺失或超长: run_id={}", request.run_id)
            return False
        if self._lease_validator is None:
            logger.error("结果租约校验器未配置，拒绝状态更新: run_id={}", request.run_id)
            return False
        return True

    async def _is_current(self, request: ResultCommitRequest) -> bool:
        assert self._lease_validator is not None
        return await self._lease_validator(request.worker_id, request.lease_id)

    async def _commit_locked(
        self,
        request: ResultCommitRequest,
        lease_is_current: bool,
    ) -> ResultCommitOutcome:
        async with in_transaction("default") as connection:
            execution = await self._locked_execution(request.run_id, connection)
            if execution is None:
                logger.warning("执行记录不存在: {}", request.run_id)
                return _rejected_outcome(request.run_id)
            if not await self._matches_worker(execution, request.worker_id, connection):
                logger.warning("拒绝非归属 Worker 结果: run_id={} worker_id={}", request.run_id, request.worker_id)
                return _rejected_outcome(execution.run_id)
            if execution.lease_id != request.lease_id:
                logger.warning("拒绝非绑定代际结果: run_id={} lease_id={}", request.run_id, request.lease_id)
                return _rejected_outcome(execution.run_id)
            if not lease_is_current and request.runtime_status not in _RUNTIME_TERMINAL:
                logger.warning("拒绝已过期代际的非终态结果: run_id={}", request.run_id)
                return _rejected_outcome(execution.run_id)
            decision = _result_update_decision(execution, request)
            if not decision.accepted:
                return _rejected_outcome(execution.run_id)
            if decision.updates:
                updated = await (
                    TaskRun.filter(id=execution.id, lease_id=request.lease_id)
                    .using_db(connection)
                    .update(**decision.updates)
                )
                if updated != 1:
                    raise RuntimeError(f"结果提交行锁不变量被破坏: run_id={execution.run_id}")
            return ResultCommitOutcome(True, execution.run_id, decision.runtime_status)

    @staticmethod
    async def _locked_execution(run_id: str, connection: Any) -> TaskRun | None:
        normalized = str(run_id)
        execution = await TaskRun.filter(run_id=normalized).using_db(connection).select_for_update().first()
        if execution is not None or len(normalized) > MAX_PUBLIC_ID_LENGTH:
            return execution
        return await TaskRun.filter(public_id=normalized).using_db(connection).select_for_update().first()

    @staticmethod
    async def _matches_worker(execution: TaskRun, worker_id: str, connection: Any) -> bool:
        if execution.worker_id is None:
            return False
        return await Worker.filter(id=execution.worker_id, public_id=worker_id).using_db(connection).exists()


def _result_update_decision(execution: TaskRun, request: ResultCommitRequest) -> _UpdateDecision:
    progress = request.runtime_status in _PROGRESS_STATUSES
    if _dispatch_blocks_result(execution):
        return _UpdateDecision(progress, {}, execution.runtime_status)
    effective_request, updates = _normalize_result_metadata(execution, request)
    progress = effective_request.runtime_status in _PROGRESS_STATUSES
    current = execution.runtime_status
    if current in _RUNTIME_TERMINAL and current != effective_request.runtime_status:
        return _UpdateDecision(progress, {}, current)
    if _is_stale_runtime_transition(execution, effective_request):
        accepted = progress or current == effective_request.runtime_status
        return _UpdateDecision(accepted, {}, current)
    _apply_dispatch_update(execution, effective_request, updates)
    _apply_runtime_update(execution, effective_request, updates)
    runtime_status = updates.get("runtime_status", current)
    return _UpdateDecision(True, updates, runtime_status)


def _normalize_result_metadata(
    execution: TaskRun,
    request: ResultCommitRequest,
) -> tuple[ResultCommitRequest, dict[str, Any]]:
    try:
        return request, request.metadata_builder(execution)
    except ResultMetadataRejected as exc:
        logger.warning("结果 metadata 被拒绝: run_id={} error={}", request.run_id, exc)
        return (
            replace(
                request,
                runtime_status=RuntimeStatus.FAILED,
                error_message=str(exc),
            ),
            {},
        )


def _rejected_outcome(run_id: str) -> ResultCommitOutcome:
    return ResultCommitOutcome(False, str(run_id), None)


def _dispatch_blocks_result(execution: TaskRun) -> bool:
    return execution.dispatch_status in _DISPATCH_FAILURE_TERMINAL and execution.runtime_status in (
        None,
        RuntimeStatus.QUEUED,
    )


def _is_stale_runtime_transition(execution: TaskRun, request: ResultCommitRequest) -> bool:
    current = execution.runtime_status
    if current is None or current in _RUNTIME_TERMINAL:
        return current in _RUNTIME_TERMINAL
    current_rank = _RUNTIME_ORDER.get(current, 0)
    incoming_rank = _RUNTIME_ORDER[request.runtime_status]
    if incoming_rank < current_rank:
        return True
    return bool(
        incoming_rank == current_rank
        and execution.runtime_updated_at
        and request.status_at <= execution.runtime_updated_at
    )


def _apply_dispatch_update(execution: TaskRun, request: ResultCommitRequest, updates: dict[str, Any]) -> None:
    if execution.dispatch_status == DispatchStatus.ACKED:
        return
    updates["dispatch_status"] = DispatchStatus.ACKED
    updates["dispatch_updated_at"] = request.status_at


def _apply_runtime_update(execution: TaskRun, request: ResultCommitRequest, updates: dict[str, Any]) -> None:
    if execution.runtime_status == request.runtime_status:
        return
    updates.update(
        runtime_status=request.runtime_status,
        runtime_updated_at=request.status_at,
        last_heartbeat=request.status_at,
        status=_TASK_STATUS_BY_RUNTIME[request.runtime_status],
    )
    if request.exit_code is not None:
        updates["exit_code"] = request.exit_code
    if request.error_message:
        updates["error_message"] = normalize_persisted_error_message(request.error_message)
    if request.runtime_status == RuntimeStatus.RUNNING and not execution.start_time:
        updates["start_time"] = request.status_at
    if request.runtime_status not in _RUNTIME_TERMINAL:
        return
    if not execution.end_time:
        updates.setdefault("end_time", request.status_at)
    start_time = execution.start_time or updates.get("start_time")
    end_time = execution.end_time or updates.get("end_time")
    if start_time and end_time and not execution.duration_seconds:
        updates.setdefault("duration_seconds", (end_time - start_time).total_seconds())


__all__ = [
    "ResultCommitOutcome",
    "ResultCommitRequest",
    "ResultMetadataRejected",
    "TaskResultCommitter",
]
