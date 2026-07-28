"""Gateway transport reconnect-manager 生命周期测试(P0-03a: 从
test_gateway_transport_connection_lifecycle.py 拆出,让原文件保持 300 行内)。
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_worker.transport.base import WorkerState
from antcode_worker.transport.gateway.reconnect import (
    ReconnectConfig,
    ReconnectManager,
    ReconnectState,
)
from antcode_worker.transport.gateway.subscriptions import SUBSCRIPTION_NAMES
from antcode_worker.transport.gateway.transport import GatewayConfig, GatewayTransport


def _channel() -> MagicMock:
    return MagicMock(channel_ready=AsyncMock(), close=AsyncMock())


def _running_transport(lease_id: str) -> GatewayTransport:
    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="worker-1"))
    transport._running = True
    transport._lease_id = lease_id
    return transport


@pytest.mark.asyncio
async def test_reconnect_manager_stop_cancels_inflight_connect():
    connect_started = asyncio.Event()

    async def blocking_connect() -> bool:
        connect_started.set()
        await asyncio.Event().wait()
        return True

    manager = ReconnectManager(
        ReconnectConfig(initial_backoff=0.001, max_backoff=0.001, jitter_factor=0),
        connect_func=blocking_connect,
    )
    await manager.start()
    manager.notify_disconnected("test")
    await asyncio.wait_for(connect_started.wait(), timeout=0.2)

    await manager.stop()

    assert manager.state == ReconnectState.STOPPED
    assert manager._reconnect_task is None


@pytest.mark.asyncio
async def test_finite_reconnect_failure_unblocks_waiter_immediately():
    manager = ReconnectManager(
        ReconnectConfig(
            initial_backoff=0.001,
            max_backoff=0.001,
            jitter_factor=0,
            max_attempts=1,
        ),
        connect_func=AsyncMock(return_value=False),
    )
    await manager.start()
    manager.notify_disconnected("test")

    assert await asyncio.wait_for(manager.wait_connected(timeout=1), timeout=0.2) is False

    assert manager.state == ReconnectState.FAILED
    await manager.stop()


@pytest.mark.asyncio
async def test_late_reconnect_waits_for_subscriptions_before_online():
    connect_started = asyncio.Event()
    release_connect = asyncio.Event()
    restored = asyncio.Event()

    async def delayed_connect() -> bool:
        connect_started.set()
        await release_connect.wait()
        return True

    transport = _running_transport("lease-1")
    transport._channel = _channel()
    transport._connected = True
    await transport._set_state(WorkerState.OFFLINE)
    manager = ReconnectManager(
        ReconnectConfig(initial_backoff=0.001, max_backoff=0.001, jitter_factor=0),
        connect_func=delayed_connect,
    )

    async def restore_online() -> None:
        await transport._restore_online_after_reconnect()
        restored.set()

    manager.on_reconnect_success(restore_online)
    await manager.start()
    manager.notify_disconnected("test")
    await asyncio.wait_for(connect_started.wait(), timeout=0.2)

    assert await manager.wait_connected(timeout=0.001) is False
    release_connect.set()
    await asyncio.wait_for(restored.wait(), timeout=0.2)

    assert transport.state == WorkerState.RECONNECTING
    transport._subscription_health = dict.fromkeys(SUBSCRIPTION_NAMES, True)
    await transport._refresh_subscription_state()
    assert transport.state == WorkerState.ONLINE
    await manager.stop()
