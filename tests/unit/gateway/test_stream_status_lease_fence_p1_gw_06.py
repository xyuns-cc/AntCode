"""P1-GW-06 回归：StreamStatus 必须在处理状态前执行 Lease fence。

缺陷 P1-GW-06：
- data_service.StreamStatus 之前只 _require_run_ownership,不 _require_current_lease,
  被撤销/换代的旧 L1 仍能上报 RUNNING/终态帧,把 L2 的实际进度覆盖或伪造。
StreamStatus 与 AckTask 对称，读取 task_status.data["lease_id"] 后调用
_require_current_lease；缺失或非 current lease 直接 abort。事务化结果提交器
的迟到终态合同由 test_task_run_lease_fencing.py 覆盖。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest
from antcode_contracts import data_pb2
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
    message = data_pb2.TaskStatus(
        worker_id="worker-a",
        run_id="r-1",
        status=data_pb2.STATUS_RUNNING,
    )

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

    message = data_pb2.TaskStatus(
        worker_id="worker-a",
        run_id="r-1",
        status=data_pb2.STATUS_RUNNING,
        data={"lease_id": "lease-stale"},
    )

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

    message = data_pb2.TaskStatus(
        worker_id="worker-a",
        run_id="r-1",
        status=data_pb2.STATUS_RUNNING,
        data={"lease_id": "lease-cur"},
    )

    await wrapped.stream_unary(_single(message), _context())

    lease_verifier.assert_awaited_once_with("worker-a", "lease-cur")
    ownership_verifier.assert_awaited_once()
    result_handler.handle.assert_awaited_once()
