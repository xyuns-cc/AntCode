from __future__ import annotations

import os
import uuid

import pytest
import redis.asyncio as aioredis
from antcode_core.infrastructure.redis.keys import RedisKeys
from antcode_core.spider_write_fence import SpiderWriteIdentity
from antcode_gateway.handlers.spider_item_writer import IdempotentSpiderItemWriter
from redis.cluster import key_slot
from redis.exceptions import ResponseError

REDIS_URL = os.getenv("ANTCODE_INTEGRATION_REDIS_URL")
pytestmark = pytest.mark.skipif(not REDIS_URL, reason="ANTCODE_INTEGRATION_REDIS_URL is required")


def _payload(run_id: str, item_id: str, sequence: int, *, title: str) -> dict:
    return {
        "item_id": item_id,
        "run_id": run_id,
        "project_id": "project-1",
        "spider_name": "antcode_rule",
        "item_type": "default",
        "data": f'{{"title":"{title}"}}'.encode(),
        "url": "https://example.com/",
        "timestamp": "2026-07-16T10:43:40Z",
        "sequence": str(sequence),
    }


async def _prime_fence(redis, identity: SpiderWriteIdentity) -> None:
    lease_key, _revoked_key, owner_key = identity.redis_keys()
    await redis.hset(
        lease_key,
        mapping={"worker_id": identity.worker_id, "lease_id": identity.lease_id, "expires_at_ms": "9999999999999"},
    )
    await redis.pexpire(lease_key, 60_000)
    await redis.set(owner_key, identity.owner_token, px=60_000)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_lua_replay_conflict_and_retention_are_atomic() -> None:
    run_id = f"gateway-idem-{uuid.uuid4()}"
    identity = SpiderWriteIdentity("antcode", "worker-1", "lease-1", run_id, "project-1")
    redis_keys = RedisKeys("antcode")
    keys = (
        redis_keys.spider_data_stream(run_id),
        redis_keys.spider_item_ids_key(run_id),
        redis_keys.spider_item_order_key(run_id),
    )
    tombstone_key = redis_keys.spider_tombstone_key(run_id)
    index_key = redis_keys.spider_index_key("project-1")
    index_expiry_key = redis_keys.spider_index_expiry_key("project-1")
    fence_keys = (*identity.redis_keys(), tombstone_key, *keys, index_key, index_expiry_key)
    assert len({key_slot(key.encode()) for key in fence_keys}) == 1
    redis = aioredis.from_url(REDIS_URL, decode_responses=False)
    writer = IdempotentSpiderItemWriter(redis, stream_max_len=2, ttl_seconds=60)
    item = _payload(run_id, "item-1", 1, title="first")
    try:
        await _prime_fence(redis, identity)
        write = {
            "identity": identity,
            "tombstone_key": tombstone_key,
            "index_key": index_key,
            "index_expiry_key": index_expiry_key,
        }
        assert (await writer.write(*keys, **write, payloads=[item])).inserted == 1
        assert (await writer.write(*keys, **write, payloads=[item])).duplicates == 1
        with pytest.raises(ResponseError, match="SPIDER_ITEM_ID_CONFLICT"):
            await writer.write(
                *keys,
                **write,
                payloads=[_payload(run_id, "item-1", 1, title="changed")],
            )

        await writer.write(*keys, **write, payloads=[_payload(run_id, "item-2", 2, title="second")])
        await writer.write(*keys, **write, payloads=[_payload(run_id, "item-3", 3, title="third")])
        assert await redis.xlen(keys[0]) == 2
        assert await redis.hlen(keys[1]) == 7
        assert await redis.zcard(keys[2]) == 2

        changed_ttl = IdempotentSpiderItemWriter(redis, stream_max_len=2, ttl_seconds=120)
        with pytest.raises(ResponseError, match="SPIDER_RETENTION_CHANGED"):
            await changed_ttl.write(*keys, **write, payloads=[item])

        with pytest.raises(ResponseError, match="SPIDER_ITEM_ID_CONFLICT"):
            await writer.write(
                *keys,
                **write,
                payloads=[
                    _payload(run_id, "item-4", 4, title="fourth"),
                    _payload(run_id, "item-3", 3, title="changed"),
                ],
            )
        assert await redis.hget(keys[1], "item:item-4") is None

        with pytest.raises(ResponseError, match="SPIDER_ITEM_ID_CONFLICT"):
            await writer.write(
                *keys,
                **write,
                payloads=[
                    _payload(run_id, "item-5", 5, title="first version"),
                    _payload(run_id, "item-5", 5, title="second version"),
                ],
            )
        assert await redis.hget(keys[1], "item:item-5") is None

        stream_before_replay = await redis.xrange(keys[0], "-", "+")
        assert (await writer.write(*keys, **write, payloads=[item])).duplicates == 1
        assert await redis.xrange(keys[0], "-", "+") == stream_before_replay

        await writer.write(
            *keys,
            **write,
            payloads=[_payload(run_id, "item-high", 100, title="high")],
        )
        await writer.write(
            *keys,
            **write,
            payloads=[_payload(run_id, "item-low", 1, title="low")],
        )
        marker_fields = set(await redis.hkeys(keys[1]))
        assert b"item:item-high" in marker_fields
        assert b"item:item-low" in marker_fields
    finally:
        await redis.delete(*fence_keys)
        await redis.aclose()


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("wrong_target", ["marker_order", "index", "index_expiry"])
async def test_lua_wrong_type_is_rejected_before_any_write(wrong_target: str) -> None:
    run_id = f"gateway-wrong-type-{uuid.uuid4()}"
    identity = SpiderWriteIdentity("antcode", "worker-1", "lease-1", run_id, "project-1")
    redis_keys = RedisKeys("antcode")
    keys = (
        redis_keys.spider_data_stream(run_id),
        redis_keys.spider_item_ids_key(run_id),
        redis_keys.spider_item_order_key(run_id),
    )
    tombstone_key = redis_keys.spider_tombstone_key(run_id)
    index_key = redis_keys.spider_index_key("project-1")
    index_expiry_key = redis_keys.spider_index_expiry_key("project-1")
    redis = aioredis.from_url(REDIS_URL, decode_responses=False)
    writer = IdempotentSpiderItemWriter(redis, stream_max_len=2, ttl_seconds=60)
    try:
        lease_key, _revoked_key, owner_key = identity.redis_keys()
        await redis.hset(
            lease_key,
            mapping={"worker_id": identity.worker_id, "lease_id": identity.lease_id, "expires_at_ms": "9999999999999"},
        )
        await redis.pexpire(lease_key, 60_000)
        await redis.set(owner_key, identity.owner_token, px=60_000)
        wrong_keys = {"marker_order": keys[2], "index": index_key, "index_expiry": index_expiry_key}
        wrong_key = wrong_keys[wrong_target]
        await redis.set(wrong_key, "wrong-type")
        with pytest.raises(ResponseError, match="SPIDER_KEY_TYPE_MISMATCH"):
            await writer.write(
                *keys,
                identity=identity,
                tombstone_key=tombstone_key,
                index_key=index_key,
                index_expiry_key=index_expiry_key,
                payloads=[_payload(run_id, "item-1", 1, title="first")],
            )
        assert await redis.xlen(keys[0]) == 0
        assert await redis.exists(keys[1]) == 0
    finally:
        await redis.delete(*keys, tombstone_key, index_key, index_expiry_key, lease_key, owner_key)
        await redis.aclose()
