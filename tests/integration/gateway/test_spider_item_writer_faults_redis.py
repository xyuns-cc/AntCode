from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Any

import pytest
import redis.asyncio as aioredis
from antcode_core.infrastructure.redis.keys import RedisKeys
from antcode_core.spider_write_fence import SpiderWriteIdentity
from antcode_gateway.handlers.spider_item_writer import IdempotentSpiderItemWriter
from redis.exceptions import ResponseError

REDIS_URL = os.getenv("ANTCODE_INTEGRATION_REDIS_URL")
pytestmark = pytest.mark.skipif(not REDIS_URL, reason="ANTCODE_INTEGRATION_REDIS_URL is required")
STREAM_MAX_LEN = 2
TTL_SECONDS = 60
BATCH_ITEM_COUNT = 3


@dataclass(frozen=True)
class _WriterCase:
    identity: SpiderWriteIdentity
    stream: str
    markers: str
    order: str
    tombstone: str
    index: str
    expiry: str

    @property
    def writer_keys(self) -> tuple[str, str, str]:
        return self.stream, self.markers, self.order

    @property
    def write_options(self) -> dict[str, Any]:
        return {
            "identity": self.identity,
            "tombstone_key": self.tombstone,
            "index_key": self.index,
            "index_expiry_key": self.expiry,
        }

    @property
    def cleanup_keys(self) -> tuple[str, ...]:
        return (*self.identity.redis_keys(), *self.writer_keys, self.tombstone, self.index, self.expiry)


def _case(prefix: str) -> _WriterCase:
    run_id = f"{prefix}-{uuid.uuid4()}"
    project_id = f"project-{uuid.uuid4()}"
    identity = SpiderWriteIdentity("antcode", "worker-1", "lease-1", run_id, project_id)
    keys = RedisKeys("antcode")
    return _WriterCase(
        identity=identity,
        stream=keys.spider_data_stream(run_id),
        markers=keys.spider_item_ids_key(run_id),
        order=keys.spider_item_order_key(run_id),
        tombstone=keys.spider_tombstone_key(run_id),
        index=keys.spider_index_key(project_id),
        expiry=keys.spider_index_expiry_key(project_id),
    )


def _payload(case: _WriterCase, item_id: str, sequence: int) -> dict[str, Any]:
    return {
        "item_id": item_id,
        "run_id": case.identity.run_id,
        "project_id": case.identity.project_id,
        "spider_name": "antcode_rule",
        "item_type": "default",
        "data": f'{{"sequence":{sequence}}}'.encode(),
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
async def test_corrupt_arrival_is_rejected_before_stream_mutation() -> None:
    case = _case("gateway-arrival")
    redis = aioredis.from_url(REDIS_URL, decode_responses=False)
    writer = IdempotentSpiderItemWriter(redis, stream_max_len=STREAM_MAX_LEN, ttl_seconds=TTL_SECONDS)
    try:
        await _prime_fence(redis, case.identity)
        await redis.hset(case.markers, "__arrival__", "broken")
        with pytest.raises(ResponseError, match="hash value is not an integer"):
            await writer.write(
                *case.writer_keys,
                **case.write_options,
                payloads=[_payload(case, "item-1", 1)],
            )
        assert await redis.xlen(case.stream) == 0
        assert await redis.hget(case.markers, "item:item-1") is None
    finally:
        await redis.delete(*case.cleanup_keys)
        await redis.aclose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_index_expiry_supports_mixed_run_retention() -> None:
    case = _case("gateway-mixed-ttl")
    redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    writer = IdempotentSpiderItemWriter(redis, stream_max_len=STREAM_MAX_LEN, ttl_seconds=TTL_SECONDS)
    try:
        await _prime_fence(redis, case.identity)
        now = (await redis.time())[0]
        stale_score = now - 1
        await redis.zadd(
            case.index,
            {case.identity.run_id: stale_score, "permanent": now - 100, "long-lived": now - 50, "expired": now - 200},
        )
        await redis.zadd(case.expiry, {"long-lived": now + 3600, "expired": now - 1})
        await writer.write(*case.writer_keys, **case.write_options, payloads=[_payload(case, "item-1", 1)])
        assert await redis.zscore(case.index, case.identity.run_id) > stale_score
        assert set(await redis.zrange(case.index, 0, -1)) == {"permanent", "long-lived", case.identity.run_id}
        assert set(await redis.zrange(case.expiry, 0, -1)) == {"long-lived", case.identity.run_id}
    finally:
        await redis.delete(*case.cleanup_keys)
        await redis.aclose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_batch_larger_than_stream_maxlen_remains_idempotent() -> None:
    case = _case("gateway-large-replay")
    redis = aioredis.from_url(REDIS_URL, decode_responses=False)
    writer = IdempotentSpiderItemWriter(redis, stream_max_len=STREAM_MAX_LEN, ttl_seconds=TTL_SECONDS)
    payloads = [_payload(case, f"item-{index}", index) for index in range(1, BATCH_ITEM_COUNT + 1)]
    try:
        await _prime_fence(redis, case.identity)
        assert (
            await writer.write(*case.writer_keys, **case.write_options, payloads=payloads)
        ).inserted == BATCH_ITEM_COUNT
        stream_after_commit = await redis.xrange(case.stream, "-", "+")
        assert len(stream_after_commit) == STREAM_MAX_LEN
        assert (
            await writer.write(*case.writer_keys, **case.write_options, payloads=payloads)
        ).duplicates == BATCH_ITEM_COUNT
        assert await redis.xrange(case.stream, "-", "+") == stream_after_commit
    finally:
        await redis.delete(*case.cleanup_keys)
        await redis.aclose()
