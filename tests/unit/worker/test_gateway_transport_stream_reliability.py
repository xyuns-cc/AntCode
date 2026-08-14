"""Gateway transport stream, authentication, and settlement reliability tests."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest
from antcode_contracts import control_pb2
from antcode_worker.transport.base import TaskResult
from antcode_worker.transport.gateway.transport import GatewayConfig, GatewayTransport


class FailedPreconditionError(RuntimeError):
    def code(self):
        return grpc.StatusCode.FAILED_PRECONDITION


class UnauthenticatedError(RuntimeError):
    def code(self):
        return grpc.StatusCode.UNAUTHENTICATED


class PermissionDeniedError(RuntimeError):
    def code(self):
        return grpc.StatusCode.PERMISSION_DENIED

    def details(self):
        return "TaskRun missing"


def _channel() -> MagicMock:
    return MagicMock(channel_ready=AsyncMock(), close=AsyncMock())


@pytest.mark.asyncio
async def test_stale_lease_rejection_keeps_control_result_retryable():
    error = FailedPreconditionError("worker lease is not current")
    stub = MagicMock(AckControl=AsyncMock(side_effect=error))
    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="worker-1"))
    transport._running = True
    transport._control_stub = stub
    transport._handle_connection_error = AsyncMock()

    sent = await transport.send_control_result(
        "request-1",
        "ignored",
        True,
        receipt="antcode:control:worker-1|1-0",
        data={},
    )

    assert sent is False
    transport._handle_connection_error.assert_awaited_once_with(error)


@pytest.mark.asyncio
async def test_permanent_status_rejection_does_not_reconnect_healthy_channel():
    error = PermissionDeniedError("TaskRun missing")
    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="worker-1"))
    transport._running = True
    transport._data_stub = MagicMock(StreamStatus=AsyncMock(side_effect=error))
    transport._handle_connection_error = AsyncMock()

    reported = await transport.report_result(TaskResult(run_id="run-1", task_id="run-1", status="cancelled"))

    assert reported is False
    assert transport._consecutive_failures == 0
    transport._handle_connection_error.assert_not_awaited()


@pytest.mark.asyncio
async def test_authentication_failure_limit_cleans_transport_resources():
    channel = _channel()
    manager = MagicMock(stop=AsyncMock())
    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="worker-1"))
    transport._running = True
    transport._channel = channel
    transport._connected = True
    transport._control_stub = MagicMock(AckControl=AsyncMock(side_effect=UnauthenticatedError("bad key")))
    transport._data_stub = MagicMock()
    transport._reconnect_manager = manager
    transport._auth_failure_count = transport._max_auth_failures - 1

    assert await transport.ack_control("event-1") is False

    assert transport.is_running is False
    assert transport.is_connected is False
    assert transport._control_stub is None
    assert transport._data_stub is None
    manager.stop.assert_awaited_once()
    channel.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_stop_cleans_resources_when_transport_is_not_running():
    channel = _channel()
    manager = MagicMock(stop=AsyncMock())
    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="worker-1"))
    transport._channel = channel
    transport._control_stub = MagicMock()
    transport._data_stub = MagicMock()
    transport._reconnect_manager = manager

    await transport.stop()

    manager.stop.assert_awaited_once()
    channel.close.assert_awaited_once()
    assert transport._channel is None


@pytest.mark.asyncio
@pytest.mark.parametrize("stream_kind", ["task", "control"])
async def test_server_stream_clean_eof_resubscribes_with_bounded_floor(monkeypatch, stream_kind):
    """W1: 空干净 EOF 用【固定 floor】退避重订阅，绝不指数增长。

    - 必须重订阅（不因空 EOF 停摆）。
    - floor 睡眠恒等于 initial_backoff，不会被推到 max_backoff(60s) 拖延投递。
    - 固定 floor 同时堵死"server 瞬时反复空 EOF → 无 sleep 忙循环 / 内存暴涨"。
    """
    sleep = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep)

    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="worker-1", initial_backoff=0.25))
    transport._running = True
    transport._lease_id = "lease-1"  # _build_subscribe_request 需要，否则 open 前即抛错

    opens = 0

    def open_stream(*_args, **_kwargs):
        nonlocal opens
        opens += 1
        if opens >= 3:
            # 第三次重订阅即停：证明空 EOF 之后确实反复重开了流。
            transport._running = False

        async def empty_stream():
            if False:
                yield None

        return empty_stream()

    if stream_kind == "task":
        transport._data_stub = MagicMock(StreamTasks=MagicMock(side_effect=open_stream))
        loop = transport._task_subscription_loop
    else:
        transport._control_stub = MagicMock(WatchControl=MagicMock(side_effect=open_stream))
        loop = transport._control_subscription_loop

    await loop()

    assert opens >= 3, "空 EOF 后未重订阅"
    # 每次空 EOF 都只睡固定 floor(0.25)，绝无指数增长（0.5 / 1.0 …）。
    assert sleep.await_args_list, "空 EOF 应加 floor 睡眠以防忙循环"
    assert all(call.args == (0.25,) for call in sleep.await_args_list), (
        f"空 EOF 退避必须恒为 floor，实际={[c.args for c in sleep.await_args_list]}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("stream_kind", ["task", "control"])
async def test_server_stream_with_data_resubscribes_immediately(monkeypatch, stream_kind):
    """W1: 流内收到过数据后的干净 EOF 立即重订阅、不 sleep（数据在流动）。"""
    sleep = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep)

    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="worker-1", initial_backoff=0.25))
    transport._running = True
    transport._lease_id = "lease-1"
    transport._task_inbox = asyncio.Queue()
    transport._control_inbox = asyncio.Queue()

    opens = 0

    def open_stream(*_args, **_kwargs):
        nonlocal opens
        opens += 1
        if opens >= 2:
            transport._running = False

        async def one_item_stream():
            yield MagicMock()

        return one_item_stream()

    if stream_kind == "task":
        transport._deliver_task_dispatch = AsyncMock()
        transport._data_stub = MagicMock(StreamTasks=MagicMock(side_effect=open_stream))
        loop = transport._task_subscription_loop
    else:
        transport._control_stub = MagicMock(WatchControl=MagicMock(side_effect=open_stream))
        loop = transport._control_subscription_loop

    await loop()

    assert opens >= 2
    sleep.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream_kind", ["task", "control"])
async def test_server_stream_error_uses_retry_delay(monkeypatch, stream_kind):
    """W1: 只有 ERROR 路径才 sleep 退避（EOF 不 sleep）。"""
    sleep = AsyncMock()

    def stop_after_sleep(_delay):
        transport._running = False

    sleep.side_effect = stop_after_sleep
    monkeypatch.setattr(asyncio, "sleep", sleep)

    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="worker-1", initial_backoff=0.25))
    transport._running = True
    transport._lease_id = "lease-1"

    def broken_stream(*_args, **_kwargs):
        async def gen():
            raise RuntimeError("stream broke")
            yield None  # pragma: no cover

        return gen()

    if stream_kind == "task":
        transport._data_stub = MagicMock(StreamTasks=MagicMock(side_effect=broken_stream))
        loop = transport._task_subscription_loop
    else:
        transport._control_stub = MagicMock(WatchControl=MagicMock(side_effect=broken_stream))
        loop = transport._control_subscription_loop

    await loop()

    sleep.assert_awaited_once_with(0.25)


@pytest.mark.asyncio
async def test_unknown_control_event_is_acked_and_skipped():
    """W3: 未知 payload 直接 ACK 跳过，不抛异常（避免 PEL 毒消息循环）。"""
    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="worker-1"))
    transport.ack_control = AsyncMock(return_value=True)
    event = MagicMock(event_id="event-unknown")
    event.WhichOneof.return_value = None

    await transport._handle_non_engine_control(event)

    transport.ack_control.assert_awaited_once_with("event-unknown")


@pytest.mark.asyncio
async def test_control_event_without_receipt_still_raises():
    """缺少 event_id 时连 ACK 都做不了，才允许继续抛异常。"""
    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="worker-1"))
    transport.ack_control = AsyncMock(return_value=True)
    event = MagicMock(event_id="")
    event.WhichOneof.return_value = None

    with pytest.raises(RuntimeError, match="无法 ACK"):
        await transport._handle_non_engine_control(event)

    transport.ack_control.assert_not_awaited()


@pytest.mark.asyncio
async def test_deregister_carries_current_lease_fence():
    stub = MagicMock(Deregister=AsyncMock(return_value=control_pb2.DeregisterResponse(success=True)))
    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="worker-1"))
    transport._control_stub = stub
    transport._lease_id = "lease-1"

    await transport.deregister("shutdown")

    request = stub.Deregister.await_args.args[0]
    assert request.worker_id == "worker-1"
    assert request.lease_id == "lease-1"


@pytest.mark.asyncio
async def test_set_credentials_refreshes_existing_authenticator_metadata():
    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="worker-old", api_key="key-old"))
    await transport._init_authenticator()

    transport.set_credentials("worker-new", "key-new")

    assert transport._get_auth_metadata() == [
        ("x-worker-id", "worker-new"),
        ("x-api-key", "key-new"),
    ]
