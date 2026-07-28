"""Real Redis tombstone fencing for Gateway Spider writes."""

from __future__ import annotations

import os
import uuid

import pytest
import redis.asyncio as aioredis
from antcode_core.infrastructure.redis.keys import RedisKeys
from antcode_core.spider_write_fence import SpiderWriteIdentity
from antcode_gateway.handlers.spider_item_writer import IdempotentSpiderItemWriter
from antcode_gateway.handlers.spider_meta_writer import SpiderMetaWriter
from redis.cluster import key_slot
from redis.exceptions import ResponseError

REDIS_URL = os.getenv("ANTCODE_INTEGRATION_REDIS_URL")
pytestmark = pytest.mark.skipif(not REDIS_URL, reason="ANTCODE_INTEGRATION_REDIS_URL is required")
MARKER_TTL_MS = 5_000


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tombstone_rejects_item_and_meta_without_recreating_run_keys() -> None:
    run_id = f"gateway-deleted-{uuid.uuid4()}"
    identity = SpiderWriteIdentity("antcode", "worker-1", "lease-1", run_id, "project-1")
    redis_keys = RedisKeys("antcode")
    stream_key = redis_keys.spider_data_stream(run_id)
    marker_key = redis_keys.spider_item_ids_key(run_id)
    order_key = redis_keys.spider_item_order_key(run_id)
    meta_key = redis_keys.spider_meta_key(run_id)
    tombstone_key = redis_keys.spider_tombstone_key(run_id)
    index_key = redis_keys.spider_index_key("project-1")
    index_expiry_key = redis_keys.spider_index_expiry_key("project-1")
    keys = (
        *identity.redis_keys(),
        stream_key,
        marker_key,
        order_key,
        meta_key,
        tombstone_key,
        index_key,
        index_expiry_key,
    )
    assert len({key_slot(key.encode()) for key in keys}) == 1
    redis = aioredis.from_url(REDIS_URL, decode_responses=False)
    item_writer = IdempotentSpiderItemWriter(redis, stream_max_len=0, ttl_seconds=0)
    meta_writer = SpiderMetaWriter(redis, ttl_seconds=0)
    payload = {
        "item_id": "item-1",
        "run_id": run_id,
        "project_id": "project-1",
        "spider_name": "rule",
        "item_type": "default",
        "data": b"{}",
        "url": "",
        "timestamp": "",
        "sequence": "1",
    }
    try:
        lease_key, _revoked_key, owner_key = identity.redis_keys()
        await redis.hset(
            lease_key,
            mapping={"worker_id": identity.worker_id, "lease_id": identity.lease_id, "expires_at_ms": "9999999999999"},
        )
        await redis.pexpire(lease_key, 60_000)
        await redis.set(owner_key, identity.owner_token, px=60_000)
        await redis.set(tombstone_key, "deleted")
        with pytest.raises(ResponseError, match="SPIDER_RUN_DELETED"):
            await item_writer.write(
                stream_key,
                marker_key,
                order_key,
                identity=identity,
                tombstone_key=tombstone_key,
                index_key=index_key,
                index_expiry_key=index_expiry_key,
                payloads=[payload],
            )
        with pytest.raises(ResponseError, match="SPIDER_RUN_DELETED"):
            await meta_writer.write(
                meta_key,
                tombstone_key,
                identity=identity,
                marker_key=marker_key,
                index_key=index_key,
                index_expiry_key=index_expiry_key,
                fields={"run_id": run_id, "project_id": "project-1"},
            )
        assert await redis.exists(stream_key, marker_key, order_key, meta_key) == 0
        assert await redis.ttl(tombstone_key) == -1
    finally:
        await redis.delete(*keys)
        await redis.aclose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_meta_wrong_index_type_has_no_partial_write() -> None:
    run_id = f"gateway-meta-index-{uuid.uuid4()}"
    project_id = f"project-{uuid.uuid4()}"
    identity = SpiderWriteIdentity("antcode", "worker-1", "lease-1", run_id, project_id)
    redis_keys = RedisKeys("antcode")
    meta_key = redis_keys.spider_meta_key(run_id)
    marker_key = redis_keys.spider_item_ids_key(run_id)
    tombstone_key = redis_keys.spider_tombstone_key(run_id)
    index_key = redis_keys.spider_index_key(project_id)
    index_expiry_key = redis_keys.spider_index_expiry_key(project_id)
    lease_key, revoked_key, owner_key = identity.redis_keys()
    cleanup_keys = (
        meta_key,
        marker_key,
        tombstone_key,
        index_key,
        index_expiry_key,
        lease_key,
        revoked_key,
        owner_key,
    )
    redis = aioredis.from_url(REDIS_URL, decode_responses=False)
    writer = SpiderMetaWriter(redis, ttl_seconds=60)
    try:
        await redis.hset(
            lease_key,
            mapping={"worker_id": identity.worker_id, "lease_id": identity.lease_id, "expires_at_ms": "9999999999999"},
        )
        await redis.pexpire(lease_key, 60_000)
        await redis.set(owner_key, identity.owner_token, px=60_000)
        await redis.set(index_key, "wrong-type")
        with pytest.raises(ResponseError, match="SPIDER_KEY_TYPE_MISMATCH index"):
            await writer.write(
                meta_key,
                tombstone_key,
                identity=identity,
                marker_key=marker_key,
                index_key=index_key,
                index_expiry_key=index_expiry_key,
                fields={"run_id": run_id, "project_id": project_id},
            )
        assert await redis.exists(meta_key, marker_key) == 0
    finally:
        await redis.delete(*cleanup_keys)
        await redis.aclose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_meta_write_refreshes_project_index_activity_score() -> None:
    run_id = f"gateway-meta-score-{uuid.uuid4()}"
    project_id = f"project-{uuid.uuid4()}"
    identity = SpiderWriteIdentity("antcode", "worker-1", "lease-1", run_id, project_id)
    redis_keys = RedisKeys("antcode")
    meta_key = redis_keys.spider_meta_key(run_id)
    marker_key = redis_keys.spider_item_ids_key(run_id)
    tombstone_key = redis_keys.spider_tombstone_key(run_id)
    index_key = redis_keys.spider_index_key(project_id)
    index_expiry_key = redis_keys.spider_index_expiry_key(project_id)
    lease_key, revoked_key, owner_key = identity.redis_keys()
    cleanup_keys = (
        meta_key,
        marker_key,
        tombstone_key,
        index_key,
        index_expiry_key,
        lease_key,
        revoked_key,
        owner_key,
    )
    redis = aioredis.from_url(REDIS_URL, decode_responses=False)
    writer = SpiderMetaWriter(redis, ttl_seconds=60)
    try:
        await redis.hset(
            lease_key,
            mapping={"worker_id": identity.worker_id, "lease_id": identity.lease_id, "expires_at_ms": "9999999999999"},
        )
        await redis.pexpire(lease_key, 60_000)
        await redis.set(owner_key, identity.owner_token, px=60_000)
        redis_time = await redis.time()
        stale_score = float(redis_time[0] - 1)
        await redis.zadd(index_key, {run_id: stale_score})
        await writer.write(
            meta_key,
            tombstone_key,
            identity=identity,
            marker_key=marker_key,
            index_key=index_key,
            index_expiry_key=index_expiry_key,
            fields={"run_id": run_id, "project_id": project_id},
        )
        assert await redis.zscore(index_key, run_id) > stale_score
    finally:
        await redis.delete(*cleanup_keys)
        await redis.aclose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_meta_write_does_not_extend_existing_item_markers() -> None:
    run_id = f"gateway-meta-marker-ttl-{uuid.uuid4()}"
    project_id = f"project-{uuid.uuid4()}"
    identity = SpiderWriteIdentity("antcode", "worker-1", "lease-1", run_id, project_id)
    redis_keys = RedisKeys("antcode")
    meta_key = redis_keys.spider_meta_key(run_id)
    marker_key = redis_keys.spider_item_ids_key(run_id)
    tombstone_key = redis_keys.spider_tombstone_key(run_id)
    index_key = redis_keys.spider_index_key(project_id)
    expiry_key = redis_keys.spider_index_expiry_key(project_id)
    lease_key, revoked_key, owner_key = identity.redis_keys()
    cleanup = (meta_key, marker_key, tombstone_key, index_key, expiry_key, lease_key, revoked_key, owner_key)
    redis = aioredis.from_url(REDIS_URL, decode_responses=False)
    writer = SpiderMetaWriter(redis, ttl_seconds=60)
    try:
        await redis.hset(
            lease_key,
            mapping={"worker_id": identity.worker_id, "lease_id": identity.lease_id, "expires_at_ms": "9999999999999"},
        )
        await redis.pexpire(lease_key, 60_000)
        await redis.set(owner_key, identity.owner_token, px=60_000)
        await redis.hset(
            marker_key,
            mapping={"__project_id__": project_id, "__ttl_seconds__": "60", "item:item-1": "digest"},
        )
        await redis.pexpire(marker_key, MARKER_TTL_MS)
        await writer.write(
            meta_key,
            tombstone_key,
            identity=identity,
            marker_key=marker_key,
            index_key=index_key,
            index_expiry_key=expiry_key,
            fields={"run_id": run_id, "project_id": project_id},
        )
        assert 0 < await redis.pttl(marker_key) <= MARKER_TTL_MS
    finally:
        await redis.delete(*cleanup)
        await redis.aclose()
