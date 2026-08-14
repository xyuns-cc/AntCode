"""Gateway TaskStatus enum and timing contract."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest
from antcode_contracts import common_pb2, data_pb2
from antcode_gateway.auth import AuthInterceptor
from antcode_gateway.services.stream_frame_guards import MAX_STATUS_ERROR_BYTES, require_bounded_status_frame

_STARTED = common_pb2.Timestamp(seconds=1_700_000_000, nanos=100)
_FINISHED = common_pb2.Timestamp(seconds=1_700_000_001, nanos=200)
_TERMINAL_STATUSES = (
    data_pb2.STATUS_COMPLETED,
    data_pb2.STATUS_FAILED,
    data_pb2.STATUS_CANCELLED,
    data_pb2.STATUS_TIMEOUT,
)


class _AbortCalled(Exception):
    pass


def _context():
    context = MagicMock()
    context.abort = AsyncMock(side_effect=_AbortCalled)
    return context


async def _single(message):
    yield message


@pytest.mark.asyncio
@pytest.mark.parametrize("status", (data_pb2.STATUS_PENDING, data_pb2.STATUS_RUNNING))
async def test_progress_statuses_accept_started_at_only(status):
    message = data_pb2.TaskStatus(status=status, started_at=_STARTED)
    context = _context()

    assert await require_bounded_status_frame(context, message) is True
    context.abort.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", _TERMINAL_STATUSES)
async def test_terminal_statuses_accept_ordered_timing(status):
    message = data_pb2.TaskStatus(
        status=status,
        started_at=_STARTED,
        finished_at=_FINISHED,
        duration_ms=1_000,
    )
    context = _context()

    assert await require_bounded_status_frame(context, message) is True
    context.abort.assert_not_awaited()


@pytest.mark.asyncio
async def test_data_service_accepts_running_status_after_identity_checks():
    from antcode_gateway.services.data_service import GatewayDataService

    result_handler = MagicMock(handle=AsyncMock(return_value=True))
    ownership_verifier = AsyncMock()
    service = GatewayDataService(
        result_handler=result_handler,
        ownership_verifier=ownership_verifier,
        lease_verifier=AsyncMock(return_value=True),
    )
    message = data_pb2.TaskStatus(
        worker_id="worker-a",
        run_id="run-status",
        status=data_pb2.STATUS_RUNNING,
        data={"lease_id": "lease-a"},
    )
    context = _context()
    original = grpc.stream_unary_rpc_method_handler(service.StreamStatus)
    wrapped = AuthInterceptor()._make_mtls_wrapped_handler(original, "worker-a")

    ack = await wrapped.stream_unary(_single(message), context)

    assert ack.received == 1
    ownership_verifier.assert_awaited_once_with("worker-a", {"run-status"})
    result_handler.handle.assert_awaited_once_with(message)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", (data_pb2.STATUS_UNSPECIFIED, 99))
async def test_unrecognized_status_is_rejected(status):
    message = data_pb2.TaskStatus(status=status)

    with pytest.raises(_AbortCalled):
        await require_bounded_status_frame(_context(), message)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    (("finished_at", _FINISHED), ("duration_ms", 1)),
)
async def test_progress_status_rejects_terminal_timing(field, value):
    message = data_pb2.TaskStatus(status=data_pb2.STATUS_RUNNING)
    if field == "finished_at":
        message.finished_at.CopyFrom(value)
    else:
        message.duration_ms = value

    with pytest.raises(_AbortCalled):
        await require_bounded_status_frame(_context(), message)


@pytest.mark.asyncio
async def test_terminal_status_requires_finished_at():
    message = data_pb2.TaskStatus(status=data_pb2.STATUS_COMPLETED, started_at=_STARTED)

    with pytest.raises(_AbortCalled):
        await require_bounded_status_frame(_context(), message)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    (
        data_pb2.TaskStatus(
            status=data_pb2.STATUS_FAILED,
            started_at=_FINISHED,
            finished_at=_STARTED,
        ),
        data_pb2.TaskStatus(
            status=data_pb2.STATUS_FAILED,
            finished_at=_FINISHED,
            duration_ms=-1,
        ),
        data_pb2.TaskStatus(
            status=data_pb2.STATUS_FAILED,
            finished_at=common_pb2.Timestamp(seconds=1, nanos=1_000_000_000),
        ),
    ),
)
async def test_terminal_status_rejects_invalid_timing(message):
    with pytest.raises(_AbortCalled):
        await require_bounded_status_frame(_context(), message)


@pytest.mark.asyncio
async def test_contract_uses_invalid_argument_status_code():
    context = _context()
    message = data_pb2.TaskStatus(status=data_pb2.STATUS_UNSPECIFIED)

    with pytest.raises(_AbortCalled):
        await require_bounded_status_frame(context, message)

    assert context.abort.await_args.args[0] is grpc.StatusCode.INVALID_ARGUMENT


@pytest.mark.asyncio
async def test_status_error_has_independent_byte_limit():
    message = data_pb2.TaskStatus(
        status=data_pb2.STATUS_FAILED,
        finished_at=_FINISHED,
        error_message="界" * MAX_STATUS_ERROR_BYTES,
    )
    context = _context()

    with pytest.raises(_AbortCalled):
        await require_bounded_status_frame(context, message)

    assert "error_message" in context.abort.await_args.args[1]
