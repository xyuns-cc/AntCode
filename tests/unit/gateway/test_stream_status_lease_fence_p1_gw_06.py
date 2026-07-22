"""P1-GW-06 回归:StreamStatus 必须做 Lease fence,迟到放行只对终态。

审查文档 docs/code-review-2026-07-22-round3-review.md 的 P1-GW-06:
- data_service.StreamStatus 之前只 _require_run_ownership,不 _require_current_lease,
  被撤销/换代的旧 L1 仍能上报 RUNNING/终态帧,把 L2 的实际进度覆盖或伪造。
- task_run_service._validate_result_lease 的"迟到放行"分支对所有状态放行,
  让旧 L1 通过 late RUNNING 帧把 L2 的运行时状态冲回去。

修复:
1. StreamStatus 与 AckTask 对称,读 task_status.data["lease_id"] 后调
   _require_current_lease 校验;缺 lease 或 lease 不 current 直接 abort。
2. _validate_result_lease 的迟到放行分支收紧:仅当 incoming_status ∈
   {SUCCESS, FAILED, CANCELLED, TIMEOUT, SKIPPED} 时放行。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest
from antcode_contracts import data_pb2
from antcode_core.application.services.task_run_service import TaskRunService
from antcode_core.domain.models.enums import RuntimeStatus
from antcode_gateway.auth import AuthInterceptor
from antcode_gateway.services.data_service import GatewayDataService


class _AbortCalled(Exception):
    pass


def _context():
    ctx = MagicMock()
    ctx.abort = AsyncMock(side_effect=_AbortCalled)
    ctx.invocation_metadata = MagicMock(return_value=[])
    return ctx


async def _single(message):
    yield message


@pytest.mark.asyncio
async def test_stream_status_rejects_missing_lease_id():
    """P1-GW-06 关键:TaskStatus 无 data['lease_id'] 时直接 FAILED_PRECONDITION。"""
    lease_verifier = AsyncMock(return_value=True)
    ownership_verifier = AsyncMock()
    result_handler = MagicMock(handle=AsyncMock(return_value=True))
    service = GatewayDataService(
        result_handler=result_handler,
        ownership_verifier=ownership_verifier,
        lease_verifier=lease_verifier,
    )
    original = grpc.stream_unary_rpc_method_handler(service.StreamStatus)
    wrapped = AuthInterceptor()._make_mtls_wrapped_handler(original, "worker-a")

    # 缺 data["lease_id"]
    message = data_pb2.TaskStatus(worker_id="worker-a", run_id="r-1")

    with pytest.raises(_AbortCalled):
        await wrapped.stream_unary(_single(message), _context())

    # lease_verifier 至少被调一次(且拿到空字符串),ownership_verifier 未被调
    ownership_verifier.assert_not_awaited()
    result_handler.handle.assert_not_awaited()


@pytest.mark.asyncio
async def test_stream_status_rejects_stale_lease():
    """P1-GW-06:lease_verifier 返回 False(旧代际) → abort。"""
    lease_verifier = AsyncMock(return_value=False)  # lease 已不 current
    ownership_verifier = AsyncMock()
    result_handler = MagicMock(handle=AsyncMock(return_value=True))
    service = GatewayDataService(
        result_handler=result_handler,
        ownership_verifier=ownership_verifier,
        lease_verifier=lease_verifier,
    )
    original = grpc.stream_unary_rpc_method_handler(service.StreamStatus)
    wrapped = AuthInterceptor()._make_mtls_wrapped_handler(original, "worker-a")

    message = data_pb2.TaskStatus(worker_id="worker-a", run_id="r-1", data={"lease_id": "lease-stale"})

    with pytest.raises(_AbortCalled):
        await wrapped.stream_unary(_single(message), _context())

    lease_verifier.assert_awaited_once_with("worker-a", "lease-stale")
    ownership_verifier.assert_not_awaited()  # 应在 lease 校验后就 abort,不到 ownership 检查
    result_handler.handle.assert_not_awaited()


@pytest.mark.asyncio
async def test_stream_status_passes_when_lease_current():
    """P1-GW-06:lease current + ownership 通过时正常处理。"""
    lease_verifier = AsyncMock(return_value=True)
    ownership_verifier = AsyncMock()
    result_handler = MagicMock(handle=AsyncMock(return_value=True))
    service = GatewayDataService(
        result_handler=result_handler,
        ownership_verifier=ownership_verifier,
        lease_verifier=lease_verifier,
    )
    original = grpc.stream_unary_rpc_method_handler(service.StreamStatus)
    wrapped = AuthInterceptor()._make_mtls_wrapped_handler(original, "worker-a")

    message = data_pb2.TaskStatus(worker_id="worker-a", run_id="r-1", data={"lease_id": "lease-cur"})

    await wrapped.stream_unary(_single(message), _context())

    lease_verifier.assert_awaited_once_with("worker-a", "lease-cur")
    ownership_verifier.assert_awaited_once()
    result_handler.handle.assert_awaited_once()


# ---------------------- _validate_result_lease 迟到放行收紧 ----------------------


class _FakeExecution:
    def __init__(self, worker_id=1, lease_id="lease-1", run_id="r-1"):
        self.worker_id = worker_id
        self.lease_id = lease_id
        self.run_id = run_id


@pytest.mark.asyncio
async def test_validate_lease_late_arrival_allows_terminal_only():
    """P1-GW-06 关键:迟到放行仅对终态(SUCCESS/FAILED/CANCELLED/TIMEOUT/SKIPPED)。"""
    service = TaskRunService(lease_validator=AsyncMock(return_value=False))  # lease 已不 current
    execution = _FakeExecution(lease_id="lease-1")

    # 终态 → 放行
    for status in (
        RuntimeStatus.SUCCESS,
        RuntimeStatus.FAILED,
        RuntimeStatus.CANCELLED,
        RuntimeStatus.TIMEOUT,
        RuntimeStatus.SKIPPED,
    ):
        ok = await service._validate_result_lease(
            execution, "worker-a", {"lease_id": "lease-1"}, incoming_status=status
        )
        assert ok is True, f"迟到 {status} 应被放行"

    # 非终态 → 拒绝
    for status in (RuntimeStatus.QUEUED, RuntimeStatus.RUNNING):
        ok = await service._validate_result_lease(
            execution, "worker-a", {"lease_id": "lease-1"}, incoming_status=status
        )
        assert ok is False, f"迟到 {status} 应被拒绝"


@pytest.mark.asyncio
async def test_validate_lease_late_arrival_rejects_when_no_status_given():
    """P1-GW-06:incoming_status=None (老调用点) 时同样拒绝迟到放行,fail-closed。"""
    service = TaskRunService(lease_validator=AsyncMock(return_value=False))
    execution = _FakeExecution(lease_id="lease-1")

    ok = await service._validate_result_lease(execution, "worker-a", {"lease_id": "lease-1"}, incoming_status=None)
    assert ok is False


@pytest.mark.asyncio
async def test_validate_lease_late_arrival_rejects_mismatched_lease():
    """P1-GW-06:incoming_lease != execution.lease 时即使终态也拒(未绑代际)。"""
    service = TaskRunService(lease_validator=AsyncMock(return_value=False))
    execution = _FakeExecution(lease_id="lease-1")

    ok = await service._validate_result_lease(
        execution, "worker-a", {"lease_id": "lease-2"}, incoming_status=RuntimeStatus.SUCCESS
    )
    assert ok is False
