from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest
from antcode_contracts import data_pb2
from antcode_gateway.auth import AuthInterceptor
from antcode_gateway.handlers.poll import TaskInfo
from antcode_gateway.services.data_service import GatewayDataService

EXPECTED_LEASE_CHECKS = 5


class _AbortCalled(RuntimeError):
    pass


def _context() -> MagicMock:
    context = MagicMock()
    context.auth_context.return_value = {}
    context.cancelled.return_value = False
    context.abort = AsyncMock(side_effect=_AbortCalled)
    context.send_initial_metadata = AsyncMock()
    return context


@pytest.mark.asyncio
async def test_stream_tasks_rechecks_lease_between_prefetched_deliveries():
    tasks = [
        TaskInfo(task_id="task-1", project_id="project-1"),
        TaskInfo(task_id="task-2", project_id="project-1"),
    ]
    poll_handler = MagicMock(handle=AsyncMock(return_value=tasks))
    lease_verifier = AsyncMock(side_effect=[True, True, True, True, False])
    service = GatewayDataService(poll_handler=poll_handler, lease_verifier=lease_verifier)
    request = data_pb2.SubscribeRequest(worker_id="worker-a", lease_id="lease-a", prefetch=2)
    original = grpc.unary_stream_rpc_method_handler(service.StreamTasks)
    wrapped = AuthInterceptor()._make_mtls_wrapped_handler(original, "worker-a")
    stream = wrapped.unary_stream(request, _context())

    assert (await anext(stream)).task_id == "task-1"
    with pytest.raises(_AbortCalled):
        await anext(stream)
    assert lease_verifier.await_count == EXPECTED_LEASE_CHECKS
