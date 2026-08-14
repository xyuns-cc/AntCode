"""Connect scheduler dispatch outcomes to atomic failure settlement."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from antcode_core.common.error_messages import normalize_persisted_error_message
from antcode_core.common.log_sanitization import sanitize_dict
from antcode_core.domain.models.enums import DispatchStatus

from antcode_master.control.failure_settlement import (
    FailurePlane,
    FailureSettlementRequest,
    settle_failure,
)
from antcode_master.control.retry_intent_delivery import deliver_retry_intent
from antcode_master.control.scheduler_authority import execute_with_scheduler_authority


async def record_dispatch_outcome(service: Any, *, execution: Any, result: dict[str, Any]):
    if result.get("success") or result.get("aborted"):
        return await execute_with_scheduler_authority(
            scheduler_epoch(execution),
            service._record_dispatch_result,
            execution=execution,
            run_id=execution.run_id,
            result=result,
        )
    return await _settle_dispatch_result_failure(service, execution, result)


async def _settle_dispatch_result_failure(service: Any, execution: Any, result: dict[str, Any]):
    message = normalize_persisted_error_message(result.get("error") or "任务执行失败") or "任务执行失败"
    safe_result = sanitize_dict(result)
    safe_result["error"] = message
    request = _dispatch_failure_request(
        execution,
        terminal_status=DispatchStatus.FAILED,
        message=message,
        expected_dispatch=frozenset({DispatchStatus.DISPATCHING}),
        result_update=safe_result,
    )
    settlement = await settle_failure(request)
    if not settlement.settled:
        await service._log_execution(execution, "DEBUG", f"忽略未取得 ownership 的派发失败: {message}")
        return None, False
    # 派发失败只落库 + 落任务日志，不发实时帧：run_status 实时帧的唯一发布者是
    # ingester/run_status_publisher（Worker 结果回传路径）。前端靠 web_api SSE 的
    # PG 权威状态兜底看到这里的 FAILED，详见 scheduler_loop 模块 docstring。
    await service._log_execution(execution, "ERROR", f"任务执行失败: {message}")
    await deliver_retry_intent(settlement.intent)
    return False, False


async def settle_unexpected_failure(
    service: Any,
    execution: Any,
    *,
    message: str,
    terminal_status: DispatchStatus,
) -> bool | None:
    request = _dispatch_failure_request(
        execution,
        terminal_status=terminal_status,
        message=message,
        expected_dispatch=frozenset({DispatchStatus.PENDING, DispatchStatus.DISPATCHING}),
    )
    settlement = await settle_failure(request)
    if not settlement.settled:
        return None
    label = "TIMEOUT" if terminal_status == DispatchStatus.TIMEOUT else "FAILED"
    await service._log_execution(execution, "ERROR", f"任务执行{label}: {message}")
    await deliver_retry_intent(settlement.intent)
    return False


def _dispatch_failure_request(
    execution: Any,
    *,
    terminal_status: DispatchStatus,
    message: str,
    expected_dispatch: frozenset[DispatchStatus],
    result_update: dict[str, Any] | None = None,
) -> FailureSettlementRequest:
    token = scheduler_epoch(execution)
    return FailureSettlementRequest(
        run_id=execution.run_id,
        authority_token=token,
        expected_scheduler_fencing_token=token,
        plane=FailurePlane.DISPATCH,
        terminal_status=terminal_status,
        error_message=message,
        status_at=datetime.now(UTC),
        expected_dispatch_statuses=expected_dispatch,
        expected_runtime_statuses=frozenset({None}),
        result_update=result_update,
    )


def scheduler_epoch(execution: Any) -> int:
    token = execution.scheduler_fencing_token
    if token is None:
        raise RuntimeError(f"scheduler-owned run 缺少 fencing token: run_id={execution.run_id}")
    return int(token)


__all__ = ["record_dispatch_outcome", "scheduler_epoch", "settle_unexpected_failure"]
