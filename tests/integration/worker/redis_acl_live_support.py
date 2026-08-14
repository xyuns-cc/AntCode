"""Lease and runtime-settlement assertions for the live Worker ACL test."""

from __future__ import annotations

import pytest
from antcode_core.application.services.lease_capability_snapshot import LeaseCapabilitySnapshot
from antcode_core.application.services.lease_fenced_ready_publish import publish_ready_batch
from antcode_core.application.services.lease_service import LeaseStore
from antcode_worker.transport.redis.runtime_control_store import (
    create_or_validate_marker,
    persist_reply_once,
)
from redis.exceptions import NoPermissionError, ResponseError

# B12: publish_ready_batch 的 Master 代际是必传参数。本用例断言的是 Worker ACL
# 拒绝写 ready stream，代际取任意合法正整数即可，不影响被断言的失败原因。
DISPATCH_EPOCH = 19


async def assert_worker_lease_access(
    admin,
    client,
    *,
    worker_id: str,
    other_lease_key: str,
) -> tuple[str, str]:
    admin_store = LeaseStore(admin, namespace="antcode")
    capabilities = {"task_types": ["code"]}
    lease = await admin_store.grant(worker_id, capabilities=capabilities)
    lease_store = LeaseStore(client, namespace="antcode")
    assert await lease_store.is_current(worker_id, lease.lease_id)
    capabilities_json = await admin.hget(admin_store.lease_key(worker_id), "capabilities_json")
    snapshot = LeaseCapabilitySnapshot(lease.lease_id, capabilities_json, lease.sequence)
    await _assert_ready_publish_denied(client, worker_id, snapshot)
    marker_key, reply_key = await _write_runtime_settlement(
        client,
        worker_id=worker_id,
        lease_id=lease.lease_id,
        expires_at_ms=lease.expires_at_ms,
    )
    await _assert_lease_mutations_denied(
        client,
        lease_store,
        worker_id=worker_id,
        lease_id=lease.lease_id,
    )
    assert await admin_store.revoke(worker_id, lease_id=lease.lease_id)
    assert await lease_store.is_current(worker_id, lease.lease_id) is False
    with pytest.raises(NoPermissionError):
        await client.hset(other_lease_key, mapping={"lease_id": "forbidden"})
    return marker_key, reply_key


async def _assert_ready_publish_denied(
    client,
    worker_id: str,
    snapshot: LeaseCapabilitySnapshot,
) -> None:
    with pytest.raises(ResponseError, match=r"ACL failure in script|No permissions to access a key"):
        await publish_ready_batch(
            client,
            worker_id=worker_id,
            snapshot=snapshot,
            messages=[{"task_id": "forged-task"}],
            scheduler_fencing_token=DISPATCH_EPOCH,
            namespace="antcode",
            worker_secret="redis-acl-ready-payload-secret-material-0001",
        )


async def _write_runtime_settlement(
    client,
    *,
    worker_id: str,
    lease_id: str,
    expires_at_ms: int,
) -> tuple[str, str]:
    marker_key = f"{{antcode}}:control:settlement:{worker_id}:acl-live"
    reply_key = f"antcode:control:reply:{worker_id}:{'a' * 32}"
    payload = {
        "request_id": f"{worker_id}:{'a' * 32}",
        "success": "true",
        "data": "{}",
        "error": "",
        "lease_id": lease_id,
        "settlement": "f" * 64,
    }
    await create_or_validate_marker(
        client,
        marker_key,
        payload,
        lease_key=f"{{antcode}}:lease:data:{worker_id}",
        expected_lease_id=lease_id,
        expires_at_ms=expires_at_ms,
    )
    await persist_reply_once(
        client,
        reply_stream=reply_key,
        message_id="1-0",
        payload=payload,
        expires_at_ms=expires_at_ms,
    )
    return marker_key, reply_key


async def _assert_lease_mutations_denied(
    client,
    lease_store,
    *,
    worker_id: str,
    lease_id: str,
) -> None:
    own_lease_key = f"{{antcode}}:lease:data:{worker_id}"
    for operation in _direct_lease_mutations(client, worker_id, own_lease_key):
        with pytest.raises(NoPermissionError):
            await operation()
    for operation in (
        lambda: lease_store.grant(worker_id, lease_id),
        lambda: lease_store.revoke(worker_id, lease_id=lease_id),
    ):
        with pytest.raises(ResponseError, match=r"script\|load"):
            await operation()
    with pytest.raises(ResponseError, match="ACL failure in script"):
        await client.eval(
            "return redis.call('HSET', KEYS[1], 'lease_id', ARGV[1])",
            1,
            own_lease_key,
            "forged",
        )


def _direct_lease_mutations(client, worker_id: str, own_lease_key: str):
    return (
        lambda: client.hset(own_lease_key, mapping={"lease_id": "forged"}),
        lambda: client.pexpire(own_lease_key, 60_000),
        lambda: client.delete(own_lease_key),
        lambda: client.sadd(f"{{antcode}}:lease:revoked:{worker_id}", "forged"),
        lambda: client.zadd("{antcode}:lease:expiring", {worker_id: 1}),
        lambda: client.sadd("{antcode}:lease:active", worker_id),
        lambda: client.incr("{antcode}:lease:sequence"),
    )


__all__ = ["assert_worker_lease_access"]
