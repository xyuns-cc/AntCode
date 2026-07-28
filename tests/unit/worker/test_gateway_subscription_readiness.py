"""Gateway subscription health must drive Worker readiness."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_worker.transport.base import WorkerState
from antcode_worker.transport.gateway.subscriptions import (
    CONTROL_SUBSCRIPTION,
    SUBSCRIPTION_NAMES,
    TASK_SUBSCRIPTION,
)
from antcode_worker.transport.gateway.transport import GatewayConfig, GatewayTransport


def _connected_transport() -> GatewayTransport:
    transport = GatewayTransport(
        gateway_config=GatewayConfig(
            worker_id="worker-1",
            initial_backoff=0.001,
            max_backoff=0.001,
        )
    )
    transport._running = True
    transport._lease_id = "lease-1"
    transport._channel = MagicMock()
    transport._connected = True
    transport._subscription_health = dict.fromkeys(SUBSCRIPTION_NAMES, True)
    transport._state = WorkerState.ONLINE
    return transport


def _broken_stream(*_args, **_kwargs):
    async def stream():
        raise RuntimeError("subscription unavailable")
        yield None  # pragma: no cover

    return stream()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stream_kind", "subscription_name"),
    [("task", TASK_SUBSCRIPTION), ("control", CONTROL_SUBSCRIPTION)],
)
async def test_permanent_subscription_failure_marks_worker_unready(
    monkeypatch,
    stream_kind: str,
    subscription_name: str,
):
    retrying = asyncio.Event()
    release_retry = asyncio.Event()

    async def blocked_retry(_delay: float) -> None:
        retrying.set()
        await release_retry.wait()

    monkeypatch.setattr(asyncio, "sleep", blocked_retry)
    transport = _connected_transport()
    if stream_kind == "task":
        transport._data_stub = MagicMock(StreamTasks=MagicMock(side_effect=_broken_stream))
        loop = transport._task_subscription_loop
    else:
        transport._control_stub = MagicMock(WatchControl=MagicMock(side_effect=_broken_stream))
        loop = transport._control_subscription_loop
    readiness: list[bool] = []
    transport.on_state_change(lambda _old, new: readiness.append(new == WorkerState.ONLINE))

    subscriber = asyncio.create_task(loop())
    await asyncio.wait_for(retrying.wait(), timeout=0.2)

    assert transport.state == WorkerState.RECONNECTING
    assert transport.is_connected is False
    assert readiness == [False]
    assert transport.get_status()["subscription_health"][subscription_name] is False

    transport._running = False
    release_retry.set()
    await subscriber


@pytest.mark.asyncio
async def test_subscription_recovery_restores_worker_readiness(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    transport = _connected_transport()
    unhealthy = asyncio.Event()
    recovered = asyncio.Event()
    hold_stream = asyncio.Event()
    states: list[WorkerState] = []

    def record_state(_old: WorkerState, new: WorkerState) -> None:
        states.append(new)
        (recovered if new == WorkerState.ONLINE else unhealthy).set()

    opens = 0

    def open_stream(*_args, **_kwargs):
        nonlocal opens
        opens += 1
        if opens == 1:
            return _broken_stream()

        async def healthy_stream():
            await hold_stream.wait()
            if False:
                yield None

        return healthy_stream()

    transport.on_state_change(record_state)
    transport._data_stub = MagicMock(StreamTasks=MagicMock(side_effect=open_stream))
    subscriber = asyncio.create_task(transport._task_subscription_loop())

    await asyncio.wait_for(unhealthy.wait(), timeout=0.2)
    await asyncio.wait_for(recovered.wait(), timeout=0.2)

    assert states[:2] == [WorkerState.RECONNECTING, WorkerState.ONLINE]
    assert transport.is_connected is True

    transport._running = False
    subscriber.cancel()
    with pytest.raises(asyncio.CancelledError):
        await subscriber
