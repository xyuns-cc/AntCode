"""Atomically settle scheduler-owned failures and durable retry intent."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Any, cast

from antcode_core.application.services.scheduler.task_run_counters import task_run_outcome_counts
from antcode_core.common.config import settings
from antcode_core.common.error_messages import normalize_persisted_error_message
from antcode_core.domain.models.enums import DispatchStatus, RuntimeStatus, TaskStatus
from antcode_core.domain.models.task import Task
from antcode_core.domain.models.task_run import TASK_ID_ABSENT, TaskRun
from tortoise.transactions import in_transaction

from antcode_master.control.result_metadata import merge_result_data
from antcode_master.control.retry_decision_history import retry_was_decided
from antcode_master.control.retry_intent_guard import RetryIntent
from antcode_master.control.scheduler_authority import require_scheduler_authority


class FailurePlane(StrEnum):
    DISPATCH = "dispatch"
    RUNTIME = "runtime"


@dataclass(frozen=True, slots=True)
class _AnyOwnership:
    """Sentinel that differs from an explicit expected NULL binding."""


ANY_OWNERSHIP = _AnyOwnership()
OwnershipExpectation = int | str | None | _AnyOwnership


@dataclass(frozen=True, slots=True)
class FailureSettlementRequest:
    run_id: str
    authority_token: int
    expected_scheduler_fencing_token: int | None
    plane: FailurePlane
    terminal_status: DispatchStatus | RuntimeStatus
    error_message: str
    status_at: datetime
    expected_dispatch_statuses: frozenset[DispatchStatus | None]
    expected_runtime_statuses: frozenset[RuntimeStatus | None]
    expected_worker_id: OwnershipExpectation = ANY_OWNERSHIP
    expected_lease_id: OwnershipExpectation = ANY_OWNERSHIP
    result_update: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "expected_dispatch_statuses", frozenset(self.expected_dispatch_statuses))
        object.__setattr__(self, "expected_runtime_statuses", frozenset(self.expected_runtime_statuses))
        if self.result_update is not None:
            object.__setattr__(self, "result_update", MappingProxyType(dict(self.result_update)))
        _validate_request(self)


@dataclass(frozen=True, slots=True)
class FailureSettlementResult:
    settled: bool
    intent: RetryIntent | None = None


@dataclass(frozen=True, slots=True)
class _RetryDecision:
    intent: RetryIntent | None
    retry_count: int
    next_retry_at: datetime | None
    result_data: dict[str, Any]


_DISPATCH_FAILURES = frozenset({DispatchStatus.REJECTED, DispatchStatus.TIMEOUT, DispatchStatus.FAILED})
_RUNTIME_FAILURES = frozenset({RuntimeStatus.FAILED, RuntimeStatus.TIMEOUT})
_RUNTIME_TERMINALS = frozenset(
    {RuntimeStatus.SUCCESS, RuntimeStatus.FAILED, RuntimeStatus.CANCELLED, RuntimeStatus.TIMEOUT, RuntimeStatus.SKIPPED}
)
_TASK_TERMINALS = frozenset(
    {
        TaskStatus.SUCCESS,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.TIMEOUT,
        TaskStatus.REJECTED,
        TaskStatus.SKIPPED,
    }
)
_DISPATCH_TO_TASK = {
    DispatchStatus.REJECTED: TaskStatus.REJECTED,
    DispatchStatus.TIMEOUT: TaskStatus.TIMEOUT,
    DispatchStatus.FAILED: TaskStatus.FAILED,
}
_RUNTIME_TO_TASK = {RuntimeStatus.FAILED: TaskStatus.FAILED, RuntimeStatus.TIMEOUT: TaskStatus.TIMEOUT}


def _validate_request(request: FailureSettlementRequest) -> None:
    if not request.run_id or request.authority_token < 1:
        raise ValueError("run_id and a positive authority_token are required")
    if not isinstance(request.plane, FailurePlane):
        raise ValueError("plane must be a FailurePlane")
    if request.status_at.tzinfo is None or request.status_at.utcoffset() is None:
        raise ValueError("status_at must be timezone-aware")
    if not request.error_message:
        raise ValueError("error_message is required")
    if not request.expected_dispatch_statuses or not request.expected_runtime_statuses:
        raise ValueError("dispatch and runtime snapshots must not be empty")
    if request.plane == FailurePlane.DISPATCH and request.terminal_status not in _DISPATCH_FAILURES:
        raise ValueError("invalid dispatch failure terminal")
    if request.plane == FailurePlane.RUNTIME and request.terminal_status not in _RUNTIME_FAILURES:
        raise ValueError("invalid runtime failure terminal")
    if "retry_intent" in (request.result_update or {}):
        raise ValueError("result_update must not provide server-authored retry_intent")


async def settle_failure(request: FailureSettlementRequest) -> FailureSettlementResult:
    """Settle one failure and its retry fact in the same PostgreSQL transaction."""
    async with in_transaction("default") as connection:
        await require_scheduler_authority(connection, request.authority_token)
        hint = await TaskRun.filter(run_id=request.run_id).using_db(connection).only("task_id").first()
        if hint is None:
            return FailureSettlementResult(False)
        task = await _lock_owning_task(connection, hint.task_id)
        if task is None and hint.task_id != TASK_ID_ABSENT:
            return FailureSettlementResult(False)
        run = await TaskRun.filter(run_id=request.run_id).using_db(connection).select_for_update().first()
        if run is None or not _matches_request(task, run, request):
            return FailureSettlementResult(False)
        decision = _decide_retry(task, run, request)
        updates = _settlement_updates(run, request, decision)
        updated = await _cas_update(connection, run=run, request=request, updates=updates)
        if updated != 1:
            return FailureSettlementResult(False)
        await _sync_task(connection, task=task, run_id=run.id, status=updates["status"])
        return FailureSettlementResult(True, decision.intent)


async def _lock_owning_task(connection: Any, task_id: int) -> Task | None:
    """锁住 run 归属的 Task。批次 run 以 ``TASK_ID_ABSENT`` 哨兵创建，``scheduled_tasks`` 里
    永远没有对应行；把"取不到 Task"一律当 settle 失败，会让 no-ack / 超时 / 失租 / 僵尸等
    全部 reaper 对批次 run 永久无效——run 卡非终态且无人能收敛，Worker 因此永远删不掉。
    """
    if task_id == TASK_ID_ABSENT:
        return None
    return await Task.filter(id=task_id).using_db(connection).select_for_update().first()


def _matches_request(task: Task | None, run: TaskRun, request: FailureSettlementRequest) -> bool:
    return all(
        (
            run.task_id == (TASK_ID_ABSENT if task is None else task.id),
            run.scheduler_fencing_token == request.expected_scheduler_fencing_token,
            run.cancel_requested_at is None,
            run.status not in _TASK_TERMINALS,
            run.dispatch_status in request.expected_dispatch_statuses,
            run.runtime_status in request.expected_runtime_statuses,
            _ownership_matches(run.worker_id, request.expected_worker_id),
            _ownership_matches(run.lease_id, request.expected_lease_id),
            not _plane_is_terminal(run, request.plane),
        )
    )


def _ownership_matches(actual: int | str | None, expected: OwnershipExpectation) -> bool:
    return expected is ANY_OWNERSHIP or actual == expected


def _plane_is_terminal(run: TaskRun, plane: FailurePlane) -> bool:
    if plane == FailurePlane.DISPATCH:
        return (
            run.dispatch_status
            in {
                DispatchStatus.ACKED,
                DispatchStatus.REJECTED,
                DispatchStatus.TIMEOUT,
                DispatchStatus.FAILED,
            }
            or run.runtime_status in _RUNTIME_TERMINALS
        )
    return run.runtime_status in _RUNTIME_TERMINALS or run.dispatch_status in _DISPATCH_FAILURES


def _decide_retry(task: Task | None, run: TaskRun, request: FailureSettlementRequest) -> _RetryDecision:
    result_data = _merge_checked(run.result_data, request.result_update)
    retry_count = int(run.retry_count or 0)
    # 无 Task ⇒ 无任务级重试策略；补派归 redispatch_service，RetryIntent 也需真实 task_id。
    if task is None:
        return _RetryDecision(None, retry_count, None, result_data)
    retry_limit = int(task.retry_count or 0)
    if not task.is_active or retry_limit <= 0 or retry_count > retry_limit:
        return _RetryDecision(None, retry_count, None, result_data)
    if run.next_retry_at is not None:
        intent = RetryIntent(task.id, run.run_id, retry_count, run.next_retry_at)
        return _RetryDecision(intent, retry_count, run.next_retry_at, result_data)
    if retry_was_decided(run) or retry_count >= retry_limit:
        return _RetryDecision(None, retry_count, None, result_data)
    retry_count += 1
    retry_delay = int(task.retry_delay or settings.TASK_RETRY_DELAY)
    retry_time = request.status_at + timedelta(seconds=retry_delay)
    marker = {
        "source_run_id": run.run_id,
        "retry_count": retry_count,
        "retry_time": retry_time.isoformat(),
    }
    result_data = _merge_checked(result_data, {"retry_intent": marker})
    intent = RetryIntent(task.id, run.run_id, retry_count, retry_time)
    return _RetryDecision(intent, retry_count, retry_time, result_data)


def _merge_checked(current: dict[str, Any] | None, update: Mapping[str, Any] | None) -> dict[str, Any]:
    normalized = dict(update or {})
    merged = merge_result_data(current, normalized)
    if any(merged.get(key) != value for key, value in normalized.items()):
        raise RuntimeError("result_data merge rejected required failure settlement metadata")
    return merged


def _settlement_updates(
    run: TaskRun,
    request: FailureSettlementRequest,
    decision: _RetryDecision,
) -> dict[str, Any]:
    duration = None if run.start_time is None else (request.status_at - run.start_time).total_seconds()
    updates: dict[str, Any] = {
        "status": _overall_status(request),
        "end_time": request.status_at,
        "duration_seconds": duration,
        "error_message": normalize_persisted_error_message(request.error_message),
        "retry_count": decision.retry_count,
        "next_retry_at": decision.next_retry_at,
        "result_data": decision.result_data,
    }
    if request.plane == FailurePlane.DISPATCH:
        updates.update(dispatch_status=request.terminal_status, dispatch_updated_at=request.status_at)
    else:
        updates.update(
            runtime_status=request.terminal_status,
            runtime_updated_at=request.status_at,
            last_heartbeat=request.status_at,
        )
    return updates


def _overall_status(request: FailureSettlementRequest) -> TaskStatus:
    if request.plane == FailurePlane.DISPATCH:
        return _DISPATCH_TO_TASK[cast(DispatchStatus, request.terminal_status)]
    return _RUNTIME_TO_TASK[cast(RuntimeStatus, request.terminal_status)]


async def _cas_update(
    connection: Any,
    *,
    run: TaskRun,
    request: FailureSettlementRequest,
    updates: dict[str, Any],
) -> int:
    filters: dict[str, Any] = {
        "id": run.id,
        "cancel_requested_at__isnull": True,
        "dispatch_status": run.dispatch_status,
    }
    expected_token = request.expected_scheduler_fencing_token
    token_key = "scheduler_fencing_token__isnull" if expected_token is None else "scheduler_fencing_token"
    filters[token_key] = True if expected_token is None else expected_token
    filters["runtime_status__isnull" if run.runtime_status is None else "runtime_status"] = (
        True if run.runtime_status is None else run.runtime_status
    )
    if request.expected_worker_id is not ANY_OWNERSHIP:
        filters["worker_id__isnull" if request.expected_worker_id is None else "worker_id"] = (
            True if request.expected_worker_id is None else request.expected_worker_id
        )
    if request.expected_lease_id is not ANY_OWNERSHIP:
        filters["lease_id__isnull" if request.expected_lease_id is None else "lease_id"] = (
            True if request.expected_lease_id is None else request.expected_lease_id
        )
    return await TaskRun.filter(**filters).using_db(connection).update(**updates)


async def _sync_task(connection: Any, *, task: Task | None, run_id: int, status: TaskStatus) -> None:
    if task is None:
        return
    counts: dict[str, Any] = await task_run_outcome_counts(connection, task.id)
    latest = await TaskRun.filter(task_id=task.id).using_db(connection).order_by("-id").only("id").first()
    if latest is not None and latest.id == run_id:
        counts["status"] = status
    await Task.filter(id=task.id).using_db(connection).update(**counts)


__all__ = [
    "ANY_OWNERSHIP",
    "FailurePlane",
    "FailureSettlementRequest",
    "FailureSettlementResult",
    "settle_failure",
]
