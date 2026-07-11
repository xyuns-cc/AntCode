from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest
from antcode_contracts import control_pb2, data_pb2
from antcode_gateway.auth import AuthInterceptor, get_authenticated_worker_id
from antcode_gateway.services.control_service import GatewayControlService
from antcode_gateway.services.data_service import GatewayDataService


class AbortCalled(RuntimeError):
    pass


def _context() -> MagicMock:
    context = MagicMock()
    context.auth_context.return_value = {}
    context.abort = AsyncMock(side_effect=AbortCalled)
    return context


@pytest.mark.asyncio
async def test_handler_receives_server_verified_principal():
    async def handler(_request, _context):
        return get_authenticated_worker_id()

    original = grpc.unary_unary_rpc_method_handler(handler)
    wrapped = AuthInterceptor()._make_mtls_wrapped_handler(original, "worker-a")

    assert await wrapped.unary_unary(None, _context()) == "worker-a"
    assert get_authenticated_worker_id() is None


@pytest.mark.asyncio
async def test_single_argument_boolean_api_key_validator_is_rejected():
    interceptor = AuthInterceptor(api_key_validator=lambda _key: True)

    result = await interceptor._authenticate_api_key("key", "worker-a")

    assert result.success is False
    assert "绑定 Worker" in result.error


@pytest.mark.asyncio
async def test_worker_bound_api_key_validator_is_accepted():
    interceptor = AuthInterceptor(api_key_validator=lambda key, worker_id: key == "key" and worker_id == "worker-a")

    result = await interceptor._authenticate_api_key("key", "worker-a")

    assert result.success is True
    assert result.worker_id == "worker-a"


@pytest.mark.asyncio
async def test_stream_tasks_rejects_request_worker_mismatch():
    service = GatewayDataService(poll_handler=MagicMock())
    request = data_pb2.SubscribeRequest(worker_id="worker-b")
    original = grpc.unary_stream_rpc_method_handler(service.StreamTasks)
    wrapped = AuthInterceptor()._make_mtls_wrapped_handler(original, "worker-a")

    with pytest.raises(AbortCalled):
        await anext(wrapped.unary_stream(request, _context()))


@pytest.mark.asyncio
async def test_ack_control_rejects_foreign_worker_stream():
    service = GatewayControlService(lease_store=MagicMock())
    request = control_pb2.AckControlRequest(
        worker_id="worker-a",
        event_id="antcode:control:worker-b|1-0",
    )
    original = grpc.unary_unary_rpc_method_handler(service.AckControl)
    wrapped = AuthInterceptor()._make_mtls_wrapped_handler(original, "worker-a")

    with pytest.raises(AbortCalled):
        await wrapped.unary_unary(request, _context())
