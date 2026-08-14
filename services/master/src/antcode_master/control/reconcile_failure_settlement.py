"""Exact-snapshot failure settlement used by Master recovery paths."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from antcode_core.common.error_messages import normalize_persisted_error_message
from antcode_core.domain.models.enums import DispatchStatus, RuntimeStatus

from antcode_master.control.failure_settlement import (
    FailurePlane,
    FailureSettlementRequest,
    settle_failure,
)
from antcode_master.control.retry_intent_delivery import deliver_retry_intent


async def settle_dispatch_failure_snapshot(
    run: Any,
    authority_token: int,
    *,
    error_message: str,
    status_at: datetime,
) -> bool:
    request = _snapshot_request(
        run,
        authority_token,
        plane=FailurePlane.DISPATCH,
        terminal_status=DispatchStatus.FAILED,
        error_message=normalize_persisted_error_message(error_message) or "failure",
        status_at=status_at,
    )
    return await _settle_and_deliver(request)


async def settle_runtime_failure_snapshot(
    run: Any,
    authority_token: int,
    *,
    terminal_status: RuntimeStatus,
    error_message: str,
    status_at: datetime,
) -> bool:
    request = _snapshot_request(
        run,
        authority_token,
        plane=FailurePlane.RUNTIME,
        terminal_status=terminal_status,
        error_message=normalize_persisted_error_message(error_message) or "failure",
        status_at=status_at,
    )
    return await _settle_and_deliver(request)


def _snapshot_request(
    run: Any,
    authority_token: int,
    *,
    plane: FailurePlane,
    terminal_status: DispatchStatus | RuntimeStatus,
    error_message: str,
    status_at: datetime,
) -> FailureSettlementRequest:
    dispatch_status = getattr(run, "dispatch_status", None)
    runtime_status = getattr(run, "runtime_status", None)
    return FailureSettlementRequest(
        run_id=run.run_id,
        authority_token=authority_token,
        expected_scheduler_fencing_token=getattr(run, "scheduler_fencing_token", None),
        plane=plane,
        terminal_status=terminal_status,
        error_message=error_message,
        status_at=status_at,
        expected_dispatch_statuses=frozenset({dispatch_status}),
        expected_runtime_statuses=frozenset({runtime_status}),
        expected_worker_id=getattr(run, "worker_id", None),
        expected_lease_id=getattr(run, "lease_id", None),
    )


async def _settle_and_deliver(request: FailureSettlementRequest) -> bool:
    result = await settle_failure(request)
    if not result.settled:
        return False
    await deliver_retry_intent(result.intent)
    return True


__all__ = ["settle_dispatch_failure_snapshot", "settle_runtime_failure_snapshot"]
