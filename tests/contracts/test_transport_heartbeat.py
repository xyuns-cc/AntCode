"""
Heartbeat contract — `send_heartbeat`.
"""

from __future__ import annotations

from datetime import datetime

import pytest

pytestmark = pytest.mark.asyncio


async def test_send_heartbeat_updates_key(transport, fresh_ids, redis_admin):
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

    if redis_admin is not None:
        keys = transport._test_keys  # type: ignore[attr-defined]
        hb_key = keys.heartbeat_key(fresh_ids.worker_id)
        exists = await redis_admin.exists(hb_key)
        assert exists == 1
        fields = await redis_admin.hgetall(hb_key)
        assert fields["status"] == "online"
        assert fields["cpu_percent"] == "12.5"
        assert fields["running_tasks"] == "2"


async def test_send_heartbeat_overwrites_previous(transport, fresh_ids, redis_admin):
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

    if redis_admin is not None:
        keys = transport._test_keys  # type: ignore[attr-defined]
        hb_key = keys.heartbeat_key(fresh_ids.worker_id)
        fields = await redis_admin.hgetall(hb_key)
        # The later send wins.
        assert fields["cpu_percent"] == "88.8"
        assert fields["running_tasks"] == "4"


async def test_send_heartbeat_sets_ttl(transport, fresh_ids, redis_admin):
    """The heartbeat key must carry a positive TTL — otherwise dead workers
    would linger forever in the cluster view."""
    from antcode_worker.transport.base import HeartbeatMessage

    hb = HeartbeatMessage(worker_id=fresh_ids.worker_id, status="online")
    assert await transport.send_heartbeat(hb) is True

    if redis_admin is not None:
        keys = transport._test_keys  # type: ignore[attr-defined]
        hb_key = keys.heartbeat_key(fresh_ids.worker_id)
        ttl = await redis_admin.ttl(hb_key)
        # Either positive seconds (TTL set) or 0/-1 are visible signals — we
        # require strictly positive: dead workers must auto-expire.
        assert ttl > 0, f"heartbeat key must have a positive TTL, got {ttl}"
