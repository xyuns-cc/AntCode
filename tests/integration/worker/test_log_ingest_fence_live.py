"""Real Redis generation interleavings for atomic log ingest."""

from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager

import pytest
import redis.asyncio as aioredis
from antcode_core.application.services.workers.log_ingest_fence import (
    LogIngestFenceRejected,
    append_fenced_log_batch,
)
from antcode_core.application.services.workers.run_ownership_fence import (
    ownership_token,
    run_owner_key,
)
from antcode_core.infrastructure.redis.control_plane import log_ingest_stream_key

REDIS_URL = os.getenv("ANTCODE_INTEGRATION_REDIS_URL")
pytestmark = pytest.mark.skipif(not REDIS_URL, reason="ANTCODE_INTEGRATION_REDIS_URL is required")
LEASE_TTL_MS = 60_000
STREAM_LENGTH_AFTER_FIRST_APPEND = 1


async def _activate_generation(redis, *, namespace: str, worker_id: str, lease_id: str, run_id: str) -> None:
    lease_key = f"{{{namespace}}}:lease:data:{worker_id}"
    await redis.hset(lease_key, mapping={"lease_id": lease_id})
    await redis.pexpire(lease_key, LEASE_TTL_MS)
    await redis.set(
        run_owner_key(run_id, namespace),
        ownership_token(worker_id, lease_id),
        px=LEASE_TTL_MS,
    )


@asynccontextmanager
async def _redis_scope():
    namespace = f"log-fence-{uuid.uuid4().hex}"
    redis = aioredis.from_url(REDIS_URL, decode_responses=False)
    keys = [
        f"{{{namespace}}}:lease:data:worker-1",
        run_owner_key("run-1", namespace),
        log_ingest_stream_key(namespace),
    ]
    try:
        yield redis, namespace
    finally:
        await redis.delete(*keys)
        await redis.aclose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_l1_append_before_takeover_is_inside_cutoff() -> None:
    async with _redis_scope() as (redis, namespace):
        await _activate_generation(
            redis,
            namespace=namespace,
            worker_id="worker-1",
            lease_id="lease-1",
            run_id="run-1",
        )
        l1_id = await append_fenced_log_batch(
            redis,
            b"l1-before-takeover",
            worker_id="worker-1",
            lease_id="lease-1",
            run_ids={"run-1"},
            namespace=namespace,
        )
        stream_info = await redis.xinfo_stream(log_ingest_stream_key(namespace))
        cutoff = stream_info.get(b"last-generated-id", stream_info.get("last-generated-id"))
        cutoff_text = cutoff.decode() if isinstance(cutoff, bytes) else str(cutoff)
        assert cutoff_text == l1_id


@pytest.mark.integration
@pytest.mark.asyncio
async def test_takeover_before_l1_append_rejects_old_generation() -> None:
    async with _redis_scope() as (redis, namespace):
        await _activate_generation(
            redis,
            namespace=namespace,
            worker_id="worker-1",
            lease_id="lease-2",
            run_id="run-1",
        )
        with pytest.raises(LogIngestFenceRejected, match="lease_stale"):
            await append_fenced_log_batch(
                redis,
                b"l1-after-takeover",
                worker_id="worker-1",
                lease_id="lease-1",
                run_ids={"run-1"},
                namespace=namespace,
            )
        stream = log_ingest_stream_key(namespace)
        assert await redis.xlen(stream) == 0

        await append_fenced_log_batch(
            redis,
            b"l2",
            worker_id="worker-1",
            lease_id="lease-2",
            run_ids={"run-1"},
            namespace=namespace,
        )
        assert await redis.xlen(stream) == STREAM_LENGTH_AFTER_FIRST_APPEND
