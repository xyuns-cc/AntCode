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


async def _single(message):
    yield message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "message", "handler_name", "handler_result", "expected_runs"),
    [
        (
            "StreamStatus",
            data_pb2.TaskStatus(worker_id="worker-a", run_id="run-status"),
            "result_handler",
            True,
            {"run-status"},
        ),
        (
            "StreamLogs",
            data_pb2.LogBatch(
                worker_id="worker-a",
                entries=[data_pb2.LogEntry(run_id="run-log")],
            ),
            "log_handler",
            True,
            {"run-log"},
        ),
        (
            "StreamSpiderData",
            data_pb2.SpiderDataBatch(
                worker_id="worker-a",
                run_id="run-spider",
                project_id="project-a",
            ),
            "spider_data_handler",
            (0, 0),
            {"run-spider"},
        ),
    ],
)
async def test_gateway_data_streams_verify_run_ownership(
    method_name,
    message,
    handler_name,
    handler_result,
    expected_runs,
):
    ownership_verifier = AsyncMock()
    handler = MagicMock()
    if method_name == "StreamStatus":
        handler.handle = AsyncMock(return_value=handler_result)
    elif method_name == "StreamLogs":
        handler.handle_log_batch = AsyncMock(return_value=handler_result)
    else:
        handler.handle_batch = AsyncMock(return_value=handler_result)
    service = GatewayDataService(
        **{handler_name: handler},
        ownership_verifier=ownership_verifier,
    )
    original = grpc.stream_unary_rpc_method_handler(getattr(service, method_name))
    wrapped = AuthInterceptor()._make_mtls_wrapped_handler(original, "worker-a")

    await wrapped.stream_unary(_single(message), _context())

    ownership_verifier.assert_awaited_once_with("worker-a", expected_runs)


@pytest.mark.asyncio
async def test_gateway_data_stream_rejects_foreign_run_before_persistence():
    ownership_verifier = AsyncMock(side_effect=PermissionError("foreign run"))
    result_handler = MagicMock(handle=AsyncMock(return_value=True))
    service = GatewayDataService(
        result_handler=result_handler,
        ownership_verifier=ownership_verifier,
    )
    original = grpc.stream_unary_rpc_method_handler(service.StreamStatus)
    wrapped = AuthInterceptor()._make_mtls_wrapped_handler(original, "worker-a")
    message = data_pb2.TaskStatus(worker_id="worker-a", run_id="run-foreign")

    with pytest.raises(AbortCalled):
        await wrapped.stream_unary(_single(message), _context())

    result_handler.handle.assert_not_awaited()
