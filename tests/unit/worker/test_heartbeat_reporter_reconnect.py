"""Heartbeat reconnect behavior."""

import pytest
from antcode_worker.heartbeat.reporter import HeartbeatReporter, HeartbeatState


class _DisconnectedTransport:
    def __init__(self):
        self.connected = False
        self.reconnect_calls = 0
        self.sent = 0

    @property
    def is_connected(self):
        return self.connected

    async def send_heartbeat(self, heartbeat):
        self.sent += 1
        return self.connected

    async def reconnect(self):
        self.reconnect_calls += 1
        self.connected = True
        return True


@pytest.mark.asyncio
async def test_disconnected_transport_counts_as_heartbeat_failure():
    transport = _DisconnectedTransport()
    reporter = HeartbeatReporter(transport=transport, worker_id="worker-1")

    success = await reporter.send_heartbeat()

    assert success is False
    assert reporter.consecutive_failures == 1
    assert transport.sent == 0


@pytest.mark.asyncio
async def test_consecutive_disconnects_trigger_reconnect():
    transport = _DisconnectedTransport()
    reporter = HeartbeatReporter(transport=transport, worker_id="worker-1")
    reporter.RECONNECT_BACKOFF_BASE = 0.0
    reporter._running = True

    for _ in range(reporter.MAX_CONSECUTIVE_FAILURES):
        await reporter.send_heartbeat()
    await reporter._handle_consecutive_failures()

    assert transport.reconnect_calls == 1
    assert transport.connected is True
    assert reporter.state == HeartbeatState.RUNNING
