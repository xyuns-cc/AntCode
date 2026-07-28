"""
Heartbeat contract — `send_heartbeat`.
"""

from __future__ import annotations

from datetime import datetime

import pytest

pytestmark = pytest.mark.asyncio


async def test_send_heartbeat_updates_key(transport, fresh_ids, contract_probe):
    """A successful heartbeat must materialize the worker's heartbeat hash."""
    from antcode_worker.transport.base import HeartbeatMessage

    hb = HeartbeatMessage(
        worker_id=fresh_ids.worker_id,
        status="online",
        cpu_percent=12.5,
        memory_percent=33.3,
        disk_percent=42.0,
        running_tasks=2,
        max_concurrent_tasks=5,
        version="test-0.0.0",
        timestamp=datetime.now(),
    )
    ok = await transport.send_heartbeat(hb)
    assert ok is True

    fields = await contract_probe.heartbeat_fields()
    assert float(fields["cpu_percent"]) == pytest.approx(12.5)
    assert int(fields["running_tasks"]) == 2


async def test_send_heartbeat_overwrites_previous(transport, fresh_ids, contract_probe):
    """Sending two heartbeats in a row must end with the later values
    visible (this is the only sane semantics for an HSET-style key)."""
    from antcode_worker.transport.base import HeartbeatMessage

    first = HeartbeatMessage(
        worker_id=fresh_ids.worker_id,
        status="online",
        cpu_percent=1.0,
        running_tasks=0,
    )
    second = HeartbeatMessage(
        worker_id=fresh_ids.worker_id,
        status="online",
        cpu_percent=88.8,
        running_tasks=4,
    )
    assert await transport.send_heartbeat(first) is True
    assert await transport.send_heartbeat(second) is True

    fields = await contract_probe.heartbeat_fields()
    assert float(fields["cpu_percent"]) == pytest.approx(88.8)
    assert int(fields["running_tasks"]) == 4


async def test_send_heartbeat_sets_ttl(transport, fresh_ids, contract_probe):
    """The heartbeat key must carry a positive TTL — otherwise dead workers
    would linger forever in the cluster view."""
    from antcode_worker.transport.base import HeartbeatMessage

    hb = HeartbeatMessage(worker_id=fresh_ids.worker_id, status="online")
    assert await transport.send_heartbeat(hb) is True

    ttl = await contract_probe.heartbeat_ttl()
    assert ttl > 0, f"heartbeat must have a positive TTL, got {ttl}"
