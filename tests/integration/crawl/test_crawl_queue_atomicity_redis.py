"""Real Redis contracts for the Crawl queue's atomic operations."""

from __future__ import annotations

import pytest
from antcode_core.application.services.crawl.backends.base import QueueTask
from antcode_core.application.services.crawl.backends.redis_keys import (
    crawl_dedup_key,
    crawl_project_deleted_key,
    crawl_stream_key,
)
from antcode_core.application.services.crawl.backends.redis_queue import (
    RedisCrawlQueueBackend,
    get_dead_letter_key,
)
from antcode_core.domain.models.enums import Priority
from antcode_core.infrastructure.redis.stream_client import StreamClient
from redis.exceptions import ResponseError

from tests.integration.crawl.redis_live_support import (
    REDIS_REQUIRED_REASON,
    REDIS_URL,
    scoped_redis,
)

GROUP = "crawl-live-workers"
DLQ_TTL_SECONDS = 7 * 24 * 60 * 60
NON_ELIGIBLE_COUNT = 21
ELIGIBLE_COUNT = 2
MIN_RECLAIM_IDLE_MS = 60_000
ELIGIBLE_IDLE_MS = 120_000

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not REDIS_URL, reason=REDIS_REQUIRED_REASON),
]


def _task(url: str) -> QueueTask:
    return QueueTask(url=url, batch_id="batch-live")


def _backend(redis, namespace: str) -> RedisCrawlQueueBackend:
    return RedisCrawlQueueBackend(
        stream_client=StreamClient(redis),
        consumer_group=GROUP,
        namespace=namespace,
    )


@pytest.mark.asyncio
async def test_unique_enqueue_is_atomic_when_xadd_fails() -> None:
    async with scoped_redis() as (redis, namespace):
        project_id = "unique"
        backend = _backend(redis, namespace)
        stream_key = crawl_stream_key(project_id, Priority.HIGH, namespace)
        dedup_key = crawl_dedup_key(project_id, namespace)
        await redis.set(stream_key, "wrong-stream-type")

        with pytest.raises(ResponseError, match="WRONGTYPE"):
            await backend.enqueue_unique(
                project_id,
                [_task("https://failed.test")],
                fingerprints=["same-fingerprint"],
                priority=Priority.HIGH,
            )
        assert not await redis.sismember(dedup_key, "same-fingerprint")

        await redis.delete(stream_key)
        results = await backend.enqueue_unique(
            project_id,
            [_task("https://first.test"), _task("https://duplicate.test")],
            fingerprints=["same-fingerprint", "same-fingerprint"],
            priority=Priority.HIGH,
        )
        assert results[0] is not None and results[1] is None
        assert await redis.xlen(stream_key) == 1
        assert await redis.scard(dedup_key) == 1


@pytest.mark.asyncio
async def test_ack_isolated_when_priority_streams_share_message_id() -> None:
    async with scoped_redis() as (redis, namespace):
        project_id = "priority-ack"
        backend = _backend(redis, namespace)
        client = StreamClient(redis)
        high_key = crawl_stream_key(project_id, Priority.HIGH, namespace)
        low_key = crawl_stream_key(project_id, Priority.LOW, namespace)
        fence_key = crawl_project_deleted_key(project_id, namespace)
        await backend.ensure_queues(project_id)

        high_id = await client.xadd(high_key, _task("https://high.test").to_dict(), msg_id="1-0")
        low_id = await client.xadd(low_key, _task("https://low.test").to_dict(), msg_id="1-0")
        assert high_id == low_id == "1-0"
        assert len(await client.xreadgroup(high_key, GROUP, "worker-high")) == 1
        assert len(await client.xreadgroup(low_key, GROUP, "worker-low")) == 1

        assert await backend.ack(project_id, ["1-0"], Priority.HIGH) == 1
        assert await redis.xlen(high_key) == 0
        assert (await client.xpending(high_key, GROUP))["pending_count"] == 0
        assert await redis.xlen(low_key) == 1
        assert (await client.xpending(low_key, GROUP))["pending_count"] == 1
        assert not await redis.exists(fence_key)


@pytest.mark.asyncio
async def test_move_pending_is_idempotent_and_sets_dlq_ttl() -> None:
    async with scoped_redis() as (redis, namespace):
        project_id = "move-pending"
        backend = _backend(redis, namespace)
        stream_key = crawl_stream_key(project_id, Priority.HIGH, namespace)
        dlq_key = get_dead_letter_key(project_id, namespace)
        await backend.ensure_queues(project_id)
        await backend.enqueue(project_id, [_task("https://dead.test")], Priority.HIGH)
        [task] = await backend.dequeue(project_id, "failed-worker", count=1, timeout_ms=0)

        first_id = await backend.dead_letter_claimed(project_id, task)
        second_id = await backend.dead_letter_claimed(project_id, task)

        assert first_id is not None and second_id is None
        assert await redis.xlen(stream_key) == 0
        assert await redis.xlen(dlq_key) == 1
        assert 0 < await redis.ttl(dlq_key) <= DLQ_TTL_SECONDS
        assert await backend.get_pending_count(project_id, Priority.HIGH) == 0


@pytest.mark.asyncio
async def test_invalid_message_is_quarantined_without_blocking_valid_message() -> None:
    async with scoped_redis() as (redis, namespace):
        project_id = "invalid-message"
        backend = _backend(redis, namespace)
        client = StreamClient(redis)
        stream_key = crawl_stream_key(project_id, Priority.HIGH, namespace)
        dlq_key = get_dead_letter_key(project_id, namespace)
        await backend.ensure_queues(project_id)
        await client.xadd(stream_key, {"url": "", "headers": {}})
        await backend.enqueue(project_id, [_task("https://valid.test")], Priority.HIGH)

        tasks = await backend.dequeue(project_id, "worker", count=2, timeout_ms=0)

        assert [task.url for task in tasks] == ["https://valid.test"]
        assert await redis.xlen(dlq_key) == 1
        assert await redis.ttl(dlq_key) > 0
        assert await redis.xlen(stream_key) == 1
        assert await backend.get_pending_count(project_id, Priority.HIGH) == 1
        assert await backend.ack(project_id, [tasks[0].msg_id], Priority.HIGH) == 1


@pytest.mark.asyncio
async def test_reclaim_scans_multiple_xautoclaim_pages_without_loss() -> None:
    async with scoped_redis() as (redis, namespace):
        project_id = "paged-reclaim"
        backend = _backend(redis, namespace)
        stream_key = crawl_stream_key(project_id, Priority.HIGH, namespace)
        total_count = NON_ELIGIBLE_COUNT + ELIGIBLE_COUNT
        await backend.ensure_queues(project_id)
        await backend.enqueue(
            project_id,
            [_task(f"https://page.test/{index}") for index in range(total_count)],
            Priority.HIGH,
        )
        claimed = await backend.dequeue(project_id, "seed-worker", count=total_count, timeout_ms=0)
        ineligible_ids = [task.msg_id for task in claimed[:NON_ELIGIBLE_COUNT]]
        eligible_ids = [task.msg_id for task in claimed[NON_ELIGIBLE_COUNT:]]
        await redis.xclaim(stream_key, GROUP, "fresh", 0, ineligible_ids, idle=1, justid=True)
        await redis.xclaim(
            stream_key,
            GROUP,
            "stale",
            0,
            eligible_ids,
            idle=ELIGIBLE_IDLE_MS,
            justid=True,
        )

        reclaimed = await backend.reclaim(
            project_id,
            min_idle_ms=MIN_RECLAIM_IDLE_MS,
            count=ELIGIBLE_COUNT,
        )

        assert [item.task.msg_id for item in reclaimed] == eligible_ids
        assert len(await redis.xpending_range(stream_key, GROUP, "-", "+", total_count)) == total_count
