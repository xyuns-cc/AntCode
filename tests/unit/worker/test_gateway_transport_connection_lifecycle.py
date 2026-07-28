"""Gateway transport connection and lease-generation lifecycle tests."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest
from antcode_contracts import control_pb2
from antcode_worker.transport.base import WorkerState
from antcode_worker.transport.gateway.reconnect import (
    ReconnectConfig,
    ReconnectManager,
    ReconnectState,
)
from antcode_worker.transport.gateway.subscriptions import SUBSCRIPTION_NAMES
from antcode_worker.transport.gateway.transport import GatewayConfig, GatewayTransport


def _capabilities_response() -> control_pb2.CapabilitiesResponse:
    return control_pb2.CapabilitiesResponse(
        runtime_control_results_v1=True,
        runtime_control_lease_fencing_v1=True,
        runtime_control_deadline_v1=True,
        artifact_transfer_v1=True,
    )


def _channel() -> MagicMock:
    return MagicMock(channel_ready=AsyncMock(), close=AsyncMock())


def test_ipv6_gateway_target_uses_brackets():
    transport = GatewayTransport(gateway_config=GatewayConfig(gateway_host="::1", gateway_port=50051))

    assert transport._gateway_target() == "[::1]:50051"


@pytest.mark.asyncio
async def test_failed_candidate_connection_preserves_active_connection(monkeypatch):
    active_channel = _channel()
    candidate_channel = _channel()
    stub = MagicMock(
        GetCapabilities=AsyncMock(return_value=control_pb2.CapabilitiesResponse(runtime_control_results_v1=False))
    )
    monkeypatch.setattr(grpc.aio, "insecure_channel", MagicMock(return_value=candidate_channel))
    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="worker-1"))
    transport._channel = active_channel
    transport._connected = True
    transport._subscription_health = dict.fromkeys(SUBSCRIPTION_NAMES, True)
    transport._create_stubs = MagicMock(return_value=(stub, MagicMock(), MagicMock()))

    assert await transport._connect() is False

    assert transport._channel is active_channel
    assert transport._connected is True
    assert transport.is_connected is True
    candidate_channel.close.assert_awaited_once()
    active_channel.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_connects_close_superseded_channel(monkeypatch):
    first_channel = _channel()
    second_channel = _channel()
    first_stub = MagicMock(GetCapabilities=AsyncMock(return_value=_capabilities_response()))
    second_stub = MagicMock(GetCapabilities=AsyncMock(return_value=_capabilities_response()))
    monkeypatch.setattr(
        grpc.aio,
        "insecure_channel",
        MagicMock(side_effect=[first_channel, second_channel]),
    )
    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="worker-1"))
    transport._create_stubs = MagicMock(
        side_effect=[
            (first_stub, MagicMock(), MagicMock()),
            (second_stub, MagicMock(), MagicMock()),
        ]
    )

    assert await asyncio.gather(transport._connect(), transport._connect()) == [True, True]

    assert transport._channel is second_channel
    first_channel.close.assert_awaited_once()
    second_channel.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconnect_reuses_same_lease(monkeypatch):
    """P1-GW-01: 重连时若 Gateway 返回相同 lease_id,正常接入 — 允许无缝续用。"""
    channel = _channel()
    stub = _control_stub(
        control_pb2.LeaseResponse(
            lease_id="lease-1",
            expires_at_ms=int(time.time() * 1000) + 30_000,
            renew_after_ms=10_000,
        )
    )
    monkeypatch.setattr(grpc.aio, "insecure_channel", MagicMock(return_value=channel))
    transport = _running_transport("lease-1")
    transport._create_stubs = MagicMock(return_value=(stub, MagicMock(), MagicMock()))

    assert await transport._connect() is True

    assert transport._lease_id == "lease-1"
    assert stub.Lease.await_args.args[0].current_lease_id == "lease-1"


@pytest.mark.asyncio
async def test_reconnect_rejects_new_lease_and_self_fences(monkeypatch):
    """P1-GW-01 关键不变量:Gateway 派新 lease_id 意味着旧代际已被剥夺,本进程必须 self-fence。

    原逻辑无条件把 self._lease_id 覆盖为 lease-2,让本进程继续跑,与真正获得 lease-2
    的 Worker 争抢 ownership/PEL,双执行同一 run。修复后本进程识别到 L1 != L2 就
    触发 _LeaseRevokedError → _abort_lease_revocation,由 Master 完整 Register 拉起
    新代际。
    """
    channel = _channel()
    stub = _control_stub(
        control_pb2.LeaseResponse(
            lease_id="lease-2",
            expires_at_ms=int(time.time() * 1000) + 30_000,
            renew_after_ms=10_000,
        )
    )
    monkeypatch.setattr(grpc.aio, "insecure_channel", MagicMock(return_value=channel))
    transport = _running_transport("lease-1")
    manager = MagicMock(stop=AsyncMock())
    transport._reconnect_manager = manager
    transport._create_stubs = MagicMock(return_value=(stub, MagicMock(), MagicMock()))

    assert await transport._connect() is False  # self-fence 后 _connect 返回 False

    assert transport._lease_id == "lease-1"  # 未被覆盖为 lease-2
    assert transport._lease_revoked is True
    assert transport.is_running is False
    manager.stop.assert_awaited_once()
    assert stub.Lease.await_args.args[0].current_lease_id == "lease-1"


@pytest.mark.asyncio
async def test_reconnect_trusts_server_lease_despite_local_clock_skew(monkeypatch):
    channel = _channel()
    stub = _control_stub(
        control_pb2.LeaseResponse(
            lease_id="lease-1",
            # 对 Worker 本机时钟看似已过期；Lease RPC 已用 Redis TIME 完成
            # 权威续租，客户端不得用另一台机器的 wall clock 再次判定。
            expires_at_ms=1,
            renew_after_ms=10_000,
        )
    )
    monkeypatch.setattr(grpc.aio, "insecure_channel", MagicMock(return_value=channel))
    transport = _running_transport("lease-1")
    transport._create_stubs = MagicMock(return_value=(stub, MagicMock(), MagicMock()))

    assert await transport._connect() is True

    assert transport._lease_id == "lease-1"
    assert transport._connected is True
    assert transport.is_connected is False
    channel.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconnect_rejects_missing_server_expiry(monkeypatch):
    channel = _channel()
    stub = _control_stub(
        control_pb2.LeaseResponse(
            lease_id="lease-1",
            expires_at_ms=0,
            renew_after_ms=10_000,
        )
    )
    monkeypatch.setattr(grpc.aio, "insecure_channel", MagicMock(return_value=channel))
    transport = _running_transport("lease-1")
    transport._create_stubs = MagicMock(return_value=(stub, MagicMock(), MagicMock()))

    assert await transport._connect() is False

    assert transport._channel is None
    assert transport.is_connected is False
    channel.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_revoked_reconnect_stops_process_without_empty_lease_regrant(monkeypatch):
    channel = _channel()
    stub = _control_stub(control_pb2.LeaseResponse(revoked=True, revoke_reason="replaced"))
    monkeypatch.setattr(grpc.aio, "insecure_channel", MagicMock(return_value=channel))
    transport = _running_transport("lease-revoked")
    manager = MagicMock(stop=AsyncMock())
    transport._reconnect_manager = manager
    transport._create_stubs = MagicMock(return_value=(stub, MagicMock(), MagicMock()))

    assert await transport._connect() is False

    assert transport._lease_id == "lease-revoked"
    assert transport._lease_revoked is True
    assert transport.is_running is False
    assert await transport.start() is False
    manager.stop.assert_awaited_once()
    assert stub.Lease.await_args.args[0].current_lease_id == "lease-revoked"


@pytest.mark.asyncio
async def test_revoked_reconnect_manager_does_not_await_itself(monkeypatch):
    channel = _channel()
    stub = _control_stub(control_pb2.LeaseResponse(revoked=True, revoke_reason="replaced"))
    monkeypatch.setattr(grpc.aio, "insecure_channel", MagicMock(return_value=channel))
    transport = _running_transport("lease-revoked")
    transport._create_stubs = MagicMock(return_value=(stub, MagicMock(), MagicMock()))
    manager = ReconnectManager(
        ReconnectConfig(initial_backoff=0.001, max_backoff=0.001, jitter_factor=0),
        connect_func=transport._connect,
    )
    transport._reconnect_manager = manager
    await manager.start()

    manager.notify_disconnected("lease check")
    reconnect_task = manager._reconnect_task
    assert reconnect_task is not None
    await asyncio.wait_for(reconnect_task, timeout=0.2)

    assert manager.state == ReconnectState.STOPPED
    assert transport._lease_revoked is True
    assert transport.is_running is False


@pytest.mark.asyncio
async def test_revoked_heartbeat_stops_without_clearing_lease_id():
    stub = MagicMock(
        Lease=AsyncMock(
            return_value=control_pb2.LeaseResponse(
                revoked=True,
                revoke_reason="replaced",
            )
        )
    )
    transport = _running_transport("lease-revoked")
    transport._control_stub = stub
    transport._channel = _channel()
    transport._connected = True
    transport._reconnect_manager = MagicMock(stop=AsyncMock())

    lease_id, _expires, _renew_after, revoked = await transport.lease_renew(current_lease_id="lease-revoked")

    assert lease_id == ""
    assert revoked is True
    assert transport._lease_id == "lease-revoked"
    assert transport._lease_revoked is True
    assert transport.is_running is False


def _control_stub(lease_response: control_pb2.LeaseResponse) -> MagicMock:
    return MagicMock(
        GetCapabilities=AsyncMock(return_value=_capabilities_response()),
        Lease=AsyncMock(return_value=lease_response),
    )


def _running_transport(lease_id: str) -> GatewayTransport:
    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="worker-1"))
    transport._running = True
    transport._lease_id = lease_id
    return transport
