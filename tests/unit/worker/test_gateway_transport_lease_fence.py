import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest
from antcode_contracts import data_pb2
from antcode_core.common.security.task_payload_envelope import seal_ready_payload
from antcode_worker.transport.base import TaskResult
from antcode_worker.transport.gateway.codecs import TaskDecoder
from antcode_worker.transport.gateway.transport import GatewayConfig, GatewayTransport

WORKER_PAYLOAD_SECRET = "gateway-transport-payload-secret-material-0001"


class FailedPreconditionError(RuntimeError):
    def code(self):
        return grpc.StatusCode.FAILED_PRECONDITION


@pytest.mark.asyncio
async def test_stale_lease_status_rejection_self_fences_immediately() -> None:
    error = FailedPreconditionError("worker lease is not current")
    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="worker-1"))
    transport._running = True
    transport._data_stub = MagicMock(StreamStatus=AsyncMock(side_effect=error))
    transport._abort_lease_revocation = AsyncMock()
    transport._handle_connection_error = AsyncMock()

    reported = await transport.report_result(TaskResult(run_id="run-1", task_id="run-1", status="failed"))

    assert reported is False
    transport._abort_lease_revocation.assert_awaited_once_with()
    transport._handle_connection_error.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_lease_connection_error_self_fences_without_reconnect() -> None:
    error = FailedPreconditionError("worker lease is not current")
    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="worker-1"))
    transport._abort_lease_revocation = AsyncMock()
    transport._reconnect_manager = MagicMock()

    await transport._handle_connection_error(error)

    transport._abort_lease_revocation.assert_awaited_once_with()
    transport._reconnect_manager.notify_disconnected.assert_not_called()


@pytest.mark.asyncio
async def test_stream_fence_stops_transport_and_invokes_callback_once() -> None:
    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="worker-1"))
    callback = AsyncMock()
    transport._running = True
    transport._lease_id = "lease-1"
    transport._lease_revoked_callback = callback
    transport._halt_transport = AsyncMock()

    await transport._abort_lease_revocation()
    await transport._abort_lease_revocation()

    assert transport._running is False
    callback.assert_awaited_once_with("gateway-revoke")
    transport._halt_transport.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_bad_dispatch_is_rejected_instead_of_entering_engine_inbox() -> None:
    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="worker-1"))
    transport._task_inbox = asyncio.Queue()
    transport.ack_task = AsyncMock(return_value=True)
    dispatch = MagicMock(receipt_id="receipt-1", task_id="task-1")
    decoder = MagicMock(decode=MagicMock(side_effect=ValueError("bad bundle")))

    await transport._deliver_task_dispatch(dispatch, decoder)

    transport.ack_task.assert_awaited_once_with(
        "receipt-1",
        accepted=False,
        reason="invalid task dispatch",
    )
    assert transport._task_inbox.empty()


@pytest.mark.asyncio
async def test_wrong_payload_key_requeues_without_sensitive_reason() -> None:
    sealed = seal_ready_payload(
        {
            "task_id": "task-1",
            "project_id": "project-1",
            "run_id": "run-1",
            "project_type": "rule",
            "params": {"token": "must-not-enter-requeue-reason"},
            "environment": {},
        },
        worker_id="worker-1",
        worker_secret=WORKER_PAYLOAD_SECRET,
    )
    dispatch = data_pb2.TaskDispatch(
        task_id="task-1",
        receipt_id="receipt-1",
        sealed_ready_payload=json.dumps(sealed, separators=(",", ":"), sort_keys=True).encode(),
    )
    transport = GatewayTransport(
        gateway_config=GatewayConfig(
            worker_id="worker-1",
            task_payload_secret="wrong-gateway-payload-secret-material-0001",
        )
    )
    transport._task_inbox = asyncio.Queue()
    transport.ack_task = AsyncMock(return_value=True)

    await transport._deliver_task_dispatch(dispatch, TaskDecoder)

    transport.ack_task.assert_awaited_once_with(
        "receipt-1",
        accepted=False,
        reason="invalid task dispatch",
    )
    assert transport._task_inbox.empty()
    assert "must-not-enter-requeue-reason" not in transport.ack_task.await_args.kwargs["reason"]


@pytest.mark.asyncio
async def test_initial_lease_conflict_remains_retryable() -> None:
    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="worker-1"))
    transport._running = True
    transport._control_stub = MagicMock(
        Lease=AsyncMock(
            side_effect=[
                SimpleNamespace(lease_id="held-lease", expires_at_ms=1, renew_after_ms=1, revoked=True),
                SimpleNamespace(lease_id="lease-2", expires_at_ms=2, renew_after_ms=1, revoked=False),
            ]
        )
    )
    transport._abort_lease_revocation = AsyncMock()
    transport._start_subscriptions = MagicMock()

    first = await transport.lease_renew("")
    second = await transport.lease_renew("")

    assert first[3] is True
    assert second[0] == "lease-2"
    assert transport._running is True
    transport._abort_lease_revocation.assert_not_awaited()


@pytest.mark.asyncio
async def test_subscription_failed_precondition_self_fences() -> None:
    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="worker-1"))
    transport._running = True
    transport._lease_id = "lease-1"

    async def fenced_stream():
        raise FailedPreconditionError("lease replaced")
        yield None

    async def revoke() -> None:
        transport._running = False

    transport._data_stub = MagicMock(StreamTasks=MagicMock(return_value=fenced_stream()))
    transport._abort_lease_revocation = AsyncMock(side_effect=revoke)

    await transport._task_subscription_loop()

    transport._abort_lease_revocation.assert_awaited_once_with()
