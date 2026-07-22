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
    context.cancelled.return_value = False
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
async def test_mtls_only_authentication_is_deferred_to_certificate_binding():
    result = await AuthInterceptor()._authenticate({"x-worker-id": "worker-a"})

    assert result.success is True
    assert result.worker_id == "worker-a"
    assert result.auth_method == "mtls"


@pytest.mark.asyncio
async def test_mtls_only_authentication_requires_a_client_certificate():
    async def handler(_request, _context):
        return "ok"

    original = grpc.unary_unary_rpc_method_handler(handler)
    wrapped = AuthInterceptor()._make_mtls_wrapped_handler(
        original,
        "worker-a",
        require_certificate=True,
    )

    with pytest.raises(AbortCalled):
        await wrapped.unary_unary(None, _context())


@pytest.mark.asyncio
async def test_mtls_only_authentication_binds_certificate_identity():
    async def handler(_request, _context):
        return get_authenticated_worker_id()

    context = _context()
    context.auth_context.return_value = {"x509_common_name": [b"worker-a"]}
    original = grpc.unary_unary_rpc_method_handler(handler)
    wrapped = AuthInterceptor()._make_mtls_wrapped_handler(
        original,
        "worker-a",
        require_certificate=True,
    )

    assert await wrapped.unary_unary(None, context) == "worker-a"


@pytest.mark.asyncio
async def test_stream_tasks_rejects_request_worker_mismatch():
    service = GatewayDataService(poll_handler=MagicMock())
    request = data_pb2.SubscribeRequest(worker_id="worker-b")
    original = grpc.unary_stream_rpc_method_handler(service.StreamTasks)
    wrapped = AuthInterceptor()._make_mtls_wrapped_handler(original, "worker-a")

    with pytest.raises(AbortCalled):
        await anext(wrapped.unary_stream(request, _context()))


@pytest.mark.asyncio
async def test_stream_tasks_rejects_stale_lease_before_poll():
    poll_handler = MagicMock(handle=AsyncMock(return_value=[]))
    lease_verifier = AsyncMock(return_value=False)
    service = GatewayDataService(
        poll_handler=poll_handler,
        lease_verifier=lease_verifier,
    )
    request = data_pb2.SubscribeRequest(
        worker_id="worker-a",
        lease_id="lease-old",
    )
    original = grpc.unary_stream_rpc_method_handler(service.StreamTasks)
    wrapped = AuthInterceptor()._make_mtls_wrapped_handler(original, "worker-a")

    with pytest.raises(AbortCalled):
        await anext(wrapped.unary_stream(request, _context()))

    lease_verifier.assert_awaited_once_with("worker-a", "lease-old")
    poll_handler.handle.assert_not_awaited()


@pytest.mark.asyncio
async def test_ack_control_rejects_foreign_worker_stream():
    service = GatewayControlService(lease_store=MagicMock())
    service._lease_store.is_current = AsyncMock(return_value=True)
    request = control_pb2.AckControlRequest(
        worker_id="worker-a",
        event_id="antcode:control:worker-b|1-0",
        lease_id="lease-a",
    )
    original = grpc.unary_unary_rpc_method_handler(service.AckControl)
    wrapped = AuthInterceptor()._make_mtls_wrapped_handler(original, "worker-a")

    with pytest.raises(AbortCalled):
        await wrapped.unary_unary(request, _context())


@pytest.mark.asyncio
async def test_get_capabilities_is_authenticated_and_side_effect_free():
    lease_store = MagicMock()
    service = GatewayControlService(lease_store=lease_store)
    request = control_pb2.CapabilitiesRequest(worker_id="worker-a")
    original = grpc.unary_unary_rpc_method_handler(service.GetCapabilities)
    wrapped = AuthInterceptor()._make_mtls_wrapped_handler(original, "worker-a")

    response = await wrapped.unary_unary(request, _context())

    assert response.runtime_control_results_v1 is True
    assert response.runtime_control_lease_fencing_v1 is True
    assert response.runtime_control_deadline_v1 is True
    assert response.artifact_transfer_v1 is True
    lease_store.grant.assert_not_called()


@pytest.mark.asyncio
async def test_ack_control_rejects_a_stale_worker_lease():
    lease_store = MagicMock()
    lease_store.is_current = AsyncMock(return_value=False)
    service = GatewayControlService(lease_store=lease_store)
    request = control_pb2.AckControlRequest(
        worker_id="worker-a",
        event_id="antcode:control:worker-a|1-0",
        lease_id="stale-lease",
    )
    original = grpc.unary_unary_rpc_method_handler(service.AckControl)
    wrapped = AuthInterceptor()._make_mtls_wrapped_handler(original, "worker-a")

    with pytest.raises(AbortCalled):
        await wrapped.unary_unary(request, _context())

    lease_store.is_current.assert_awaited_once_with("worker-a", "stale-lease")


@pytest.mark.asyncio
async def test_register_does_not_create_an_unusable_lease():
    lease_store = MagicMock()
    lease_store.policy.ttl_ms = 30_000
    lease_store.policy.renew_after_ms = 10_000
    service = GatewayControlService(lease_store=lease_store)
    service._verify_registration = AsyncMock(return_value=(True, "", MagicMock()))
    request = control_pb2.RegisterRequest(worker_id="worker-a", api_key="api-key")

    response = await service.Register(request, _context())

    assert response.success is True
    assert response.lease_ttl_ms == 30_000
    lease_store.grant.assert_not_called()


async def _single(message):
    yield message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "message", "handler_name", "handler_result", "expected_runs"),
    [
        (
            "StreamStatus",
            # P1-GW-06: TaskStatus 必须携带 data["lease_id"], StreamStatus 会与
            # AckTask 对称做 _require_current_lease 校验。
            data_pb2.TaskStatus(worker_id="worker-a", run_id="run-status", data={"lease_id": "lease-a"}),
            "result_handler",
            True,
            {"run-status"},
        ),
        (
            "StreamLogs",
            data_pb2.LogBatch(
                worker_id="worker-a",
                lease_id="lease-a",
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
                lease_id="lease-a",
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
    log_ownership_verifier = AsyncMock()
    spider_ownership_verifier = AsyncMock()
    lease_verifier = AsyncMock(return_value=True)
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
        log_ownership_verifier=log_ownership_verifier,
        spider_ownership_verifier=spider_ownership_verifier,
        lease_verifier=lease_verifier,
    )
    original = grpc.stream_unary_rpc_method_handler(getattr(service, method_name))
    wrapped = AuthInterceptor()._make_mtls_wrapped_handler(original, "worker-a")

    await wrapped.stream_unary(_single(message), _context())

    if method_name == "StreamSpiderData":
        lease_verifier.assert_awaited_once_with("worker-a", "lease-a")
        spider_ownership_verifier.assert_awaited_once_with(
            "worker-a",
            "run-spider",
            "project-a",
            lease_id="lease-a",
        )
        ownership_verifier.assert_not_awaited()
        log_ownership_verifier.assert_not_awaited()
    elif method_name == "StreamLogs":
        lease_verifier.assert_awaited_once_with("worker-a", "lease-a")
        log_ownership_verifier.assert_awaited_once_with(
            "worker-a",
            expected_runs,
            lease_id="lease-a",
        )
        ownership_verifier.assert_not_awaited()
        spider_ownership_verifier.assert_not_awaited()
    else:
        # P1-GW-06: StreamStatus 也走 lease 校验
        lease_verifier.assert_awaited_once_with("worker-a", "lease-a")
        ownership_verifier.assert_awaited_once_with("worker-a", expected_runs)
        log_ownership_verifier.assert_not_awaited()
        spider_ownership_verifier.assert_not_awaited()


@pytest.mark.asyncio
async def test_gateway_data_stream_rejects_foreign_run_before_persistence():
    ownership_verifier = AsyncMock(side_effect=PermissionError("foreign run"))
    lease_verifier = AsyncMock(return_value=True)
    result_handler = MagicMock(handle=AsyncMock(return_value=True))
    service = GatewayDataService(
        result_handler=result_handler,
        ownership_verifier=ownership_verifier,
        lease_verifier=lease_verifier,
    )
    original = grpc.stream_unary_rpc_method_handler(service.StreamStatus)
    wrapped = AuthInterceptor()._make_mtls_wrapped_handler(original, "worker-a")
    # P1-GW-06: TaskStatus 现在必须带 lease_id, 否则会先被 lease 校验拒;
    # 这里带上 lease_id 让流程走到 ownership 检查再触发 PermissionError。
    message = data_pb2.TaskStatus(worker_id="worker-a", run_id="run-foreign", data={"lease_id": "lease-a"})

    with pytest.raises(AbortCalled):
        await wrapped.stream_unary(_single(message), _context())

    result_handler.handle.assert_not_awaited()


@pytest.mark.asyncio
async def test_stream_logs_maps_invalid_batch_to_invalid_argument():
    context = _context()
    context.abort = AsyncMock()
    log_handler = MagicMock(
        handle_log_batch=AsyncMock(side_effect=ValueError("LogEntry content bytes 超限")),
    )
    service = GatewayDataService(
        log_handler=log_handler,
        log_ownership_verifier=AsyncMock(),
        lease_verifier=AsyncMock(return_value=True),
    )
    original = grpc.stream_unary_rpc_method_handler(service.StreamLogs)
    wrapped = AuthInterceptor()._make_mtls_wrapped_handler(original, "worker-a")
    message = data_pb2.LogBatch(
        worker_id="worker-a",
        lease_id="lease-a",
        entries=[data_pb2.LogEntry(run_id="run-log")],
    )

    response = await wrapped.stream_unary(_single(message), context)

    assert response.received == 0
    context.abort.assert_awaited_once()
    status, detail = context.abort.await_args.args
    assert status == grpc.StatusCode.INVALID_ARGUMENT
    assert detail == "invalid log batch: LogEntry content bytes 超限"


@pytest.mark.asyncio
async def test_stream_logs_rejects_stale_lease_before_persistence():
    log_handler = MagicMock(handle_log_batch=AsyncMock(return_value=True))
    log_ownership_verifier = AsyncMock()
    service = GatewayDataService(
        log_handler=log_handler,
        log_ownership_verifier=log_ownership_verifier,
        lease_verifier=AsyncMock(return_value=False),
    )
    original = grpc.stream_unary_rpc_method_handler(service.StreamLogs)
    wrapped = AuthInterceptor()._make_mtls_wrapped_handler(original, "worker-a")
    message = data_pb2.LogBatch(
        worker_id="worker-a",
        lease_id="lease-old",
        entries=[data_pb2.LogEntry(run_id="run-log")],
    )

    with pytest.raises(AbortCalled):
        await wrapped.stream_unary(_single(message), _context())

    log_ownership_verifier.assert_not_awaited()
    log_handler.handle_log_batch.assert_not_awaited()


@pytest.mark.asyncio
async def test_stream_logs_rejects_task_run_lease_mismatch_before_persistence():
    log_handler = MagicMock(handle_log_batch=AsyncMock(return_value=True))
    log_ownership_verifier = AsyncMock(side_effect=PermissionError("TaskRun lease_id 代际不匹配"))
    service = GatewayDataService(
        log_handler=log_handler,
        log_ownership_verifier=log_ownership_verifier,
        lease_verifier=AsyncMock(return_value=True),
    )
    original = grpc.stream_unary_rpc_method_handler(service.StreamLogs)
    wrapped = AuthInterceptor()._make_mtls_wrapped_handler(original, "worker-a")
    message = data_pb2.LogBatch(
        worker_id="worker-a",
        lease_id="lease-current",
        entries=[data_pb2.LogEntry(run_id="run-log")],
    )

    with pytest.raises(AbortCalled):
        await wrapped.stream_unary(_single(message), _context())

    log_ownership_verifier.assert_awaited_once_with(
        "worker-a",
        {"run-log"},
        lease_id="lease-current",
    )
    log_handler.handle_log_batch.assert_not_awaited()


@pytest.mark.asyncio
async def test_gateway_spider_stream_rejects_project_mismatch_before_persistence():
    spider_ownership_verifier = AsyncMock(side_effect=PermissionError("project mismatch"))
    spider_data_handler = MagicMock(handle_batch=AsyncMock(return_value=(1, 0)))
    service = GatewayDataService(
        spider_data_handler=spider_data_handler,
        spider_ownership_verifier=spider_ownership_verifier,
        lease_verifier=AsyncMock(return_value=True),
    )
    original = grpc.stream_unary_rpc_method_handler(service.StreamSpiderData)
    wrapped = AuthInterceptor()._make_mtls_wrapped_handler(original, "worker-a")
    message = data_pb2.SpiderDataBatch(
        worker_id="worker-a",
        run_id="run-spider",
        project_id="foreign-project",
        lease_id="lease-a",
    )

    with pytest.raises(AbortCalled):
        await wrapped.stream_unary(_single(message), _context())

    spider_ownership_verifier.assert_awaited_once_with(
        "worker-a",
        "run-spider",
        "foreign-project",
        lease_id="lease-a",
    )
    spider_data_handler.handle_batch.assert_not_awaited()


@pytest.mark.asyncio
async def test_gateway_spider_stream_rejects_noncanonical_ids_before_persistence():
    spider_ownership_verifier = AsyncMock()
    spider_data_handler = MagicMock(handle_batch=AsyncMock(return_value=(1, 0)))
    service = GatewayDataService(
        spider_data_handler=spider_data_handler,
        spider_ownership_verifier=spider_ownership_verifier,
        lease_verifier=AsyncMock(return_value=True),
    )
    original = grpc.stream_unary_rpc_method_handler(service.StreamSpiderData)
    wrapped = AuthInterceptor()._make_mtls_wrapped_handler(original, "worker-a")
    message = data_pb2.SpiderDataBatch(
        worker_id="worker-a",
        run_id=" run-spider ",
        project_id="project-a",
        lease_id="lease-a",
    )

    with pytest.raises(AbortCalled):
        await wrapped.stream_unary(_single(message), _context())

    spider_ownership_verifier.assert_not_awaited()
    spider_data_handler.handle_batch.assert_not_awaited()
