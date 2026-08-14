"""Real Redis coverage for Lease-fenced ready-stream publication."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Any

import pytest
import pytest_asyncio
import redis.asyncio as aioredis
from antcode_core.application.services.lease_capability_snapshot import LeaseCapabilitySnapshot
from antcode_core.application.services.lease_fenced_ready_publish import (
    LeaseDispatchFenceError,
    publish_ready_batch,
)
from antcode_core.application.services.lease_service import (
    LEASE_RECORD_RETENTION_MS,
    LeasePolicy,
    LeaseStore,
)
from antcode_core.common.security.task_payload_envelope import open_ready_payload
from antcode_core.infrastructure.redis.control_plane import (
    decode_stream_payload,
    scheduler_dispatch_fencing_key,
    task_ready_stream,
)
from redis.cluster import key_slot

REDIS_URL = os.getenv("ANTCODE_INTEGRATION_REDIS_URL")
pytestmark = pytest.mark.skipif(not REDIS_URL, reason="ANTCODE_INTEGRATION_REDIS_URL is required")
CAPABILITIES = {"task_types": ["code"], "tags": ["linux"]}
LEASE_TTL_MS = 30_000
WORKER_SECRET = "worker-ready-integration-secret-material-0001"
# B12: Master 代际栅栏必须真实存在，publish 才可能通过；空 token 会被脚本直接拒绝。
CURRENT_EPOCH = 19


@dataclass(frozen=True, slots=True)
class _ReadyContext:
    redis: Any
    worker_id: str
    namespace: str
    snapshot: LeaseCapabilitySnapshot
    lease_key: str
    revoked_key: str
    stream_key: str


async def _delete_namespace(redis, namespace: str) -> None:
    for pattern in (f"{{{namespace}}}:*", f"{namespace}:*"):
        cursor = 0
        while True:
            cursor, keys = await redis.scan(cursor=cursor, match=pattern, count=500)
            if keys:
                await redis.delete(*keys)
            if cursor == 0:
                break


@pytest_asyncio.fixture
async def ready_context():
    redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    namespace = f"ready-publish-{uuid.uuid4().hex}"
    worker_id = f"worker-{uuid.uuid4().hex}"
    store = LeaseStore(redis, namespace=namespace, policy=LeasePolicy(ttl_ms=LEASE_TTL_MS))
    lease = await store.grant(worker_id, capabilities=CAPABILITIES)
    lease_key = store.lease_key(worker_id)
    capabilities_json = await redis.hget(lease_key, "capabilities_json")
    assert capabilities_json
    revoked_key = store.REVOKED_SET_TEMPLATE.format(ns=namespace, worker_id=worker_id)
    # Master 在 Leader 激活时写这把键；集成环境里没有真 Master，这里显式预置。
    await redis.set(scheduler_dispatch_fencing_key(namespace=namespace), str(CURRENT_EPOCH))
    context = _ReadyContext(
        redis=redis,
        worker_id=worker_id,
        namespace=namespace,
        snapshot=LeaseCapabilitySnapshot(lease.lease_id, capabilities_json, lease.sequence),
        lease_key=lease_key,
        revoked_key=revoked_key,
        stream_key=task_ready_stream(worker_id, namespace=namespace),
    )
    try:
        yield context
    finally:
        try:
            await _delete_namespace(redis, namespace)
        finally:
            await redis.aclose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_publish_ready_batch_writes_every_message_atomically(ready_context: _ReadyContext) -> None:
    context = ready_context
    messages = [
        {"task_id": "run-1", "priority": 0},
        {"task_id": "run-2", "params": {"attempt": 1}},
    ]

    message_ids = await publish_ready_batch(
        context.redis,
        worker_id=context.worker_id,
        snapshot=context.snapshot,
        messages=messages,
        scheduler_fencing_token=CURRENT_EPOCH,
        namespace=context.namespace,
        worker_secret=WORKER_SECRET,
    )

    entries = await context.redis.xrange(context.stream_key)
    assert [message_id for message_id, _payload in entries] == message_ids
    assert [payload["task_id"] for _message_id, payload in entries] == ["run-1", "run-2"]
    assert entries[0][1]["priority"] == "0"
    assert "params" not in entries[1][1]
    assert "attempt" not in str(entries[1][1])
    assert "sensitive_payload_envelope" in entries[1][1]
    assert all(payload["dispatch_lease_id"] == context.snapshot.lease_id for _, payload in entries)
    assert all(payload["dispatch_lease_gen"] == str(context.snapshot.lease_gen) for _, payload in entries)
    restored = open_ready_payload(
        decode_stream_payload(entries[1][1]),
        worker_id=context.worker_id,
        worker_secret=WORKER_SECRET,
    )
    assert restored["params"] == {"attempt": 1}
    assert restored["environment"] == {}


async def _break_fence(context: _ReadyContext, failure: str) -> None:
    if failure == "lease_changed":
        await context.redis.hset(context.lease_key, "lease_id", "replacement-lease")
        return
    if failure == "lease_expired":
        await context.redis.pexpire(context.lease_key, LEASE_RECORD_RETENTION_MS)
        return
    if failure == "lease_revoked":
        await context.redis.sadd(context.revoked_key, context.snapshot.lease_id)
        return
    if failure == "capabilities_changed":
        await context.redis.hset(context.lease_key, "capabilities_json", '{"task_types":["crawl"]}')
        return
    if failure == "lease_generation_changed":
        await context.redis.hincrby(context.lease_key, "sequence", 1)
        return
    raise AssertionError(f"unknown fence failure: {failure}")


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        "lease_changed",
        "lease_expired",
        "lease_revoked",
        "capabilities_changed",
        "lease_generation_changed",
    ],
)
async def test_publish_ready_batch_rejects_stale_snapshot_without_writes(
    ready_context: _ReadyContext,
    failure: str,
) -> None:
    context = ready_context
    await _break_fence(context, failure)

    with pytest.raises(LeaseDispatchFenceError, match=failure):
        await publish_ready_batch(
            context.redis,
            worker_id=context.worker_id,
            snapshot=context.snapshot,
            messages=[{"task_id": "must-not-publish"}],
            scheduler_fencing_token=CURRENT_EPOCH,
            namespace=context.namespace,
            worker_secret=WORKER_SECRET,
        )

    assert await context.redis.xlen(context.stream_key) == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_publish_ready_batch_keys_share_one_cluster_slot(ready_context: _ReadyContext) -> None:
    context = ready_context
    keys = (
        context.lease_key,
        context.revoked_key,
        context.stream_key,
        scheduler_dispatch_fencing_key(namespace=context.namespace),
    )

    assert len({key_slot(key.encode("utf-8")) for key in keys}) == 1
