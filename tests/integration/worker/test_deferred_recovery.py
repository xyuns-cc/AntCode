"""Real Redis coverage for exact-message Direct task deferral."""

import asyncio
import os
import uuid

import pytest
from antcode_worker.transport.redis.deferred_recovery import DeferredTaskRecovery

REDIS_URL = os.getenv("ANTCODE_INTEGRATION_REDIS_URL")
pytestmark = pytest.mark.skipif(
    not REDIS_URL,
    reason="ANTCODE_INTEGRATION_REDIS_URL is required for worker integration tests",
)


@pytest.mark.asyncio
async def test_defer_recovers_only_registered_current_consumer_message():
    import redis.asyncio as aioredis

    redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    suffix = uuid.uuid4().hex
    stream_key = f"antcode-test:{suffix}:ready"
    group = f"workers-{suffix}"
    consumer = f"worker-{suffix}-lease-1"
    delivered: list[tuple[str, dict[str, str]]] = []
    visible = asyncio.Event()

    async def on_visible(message_id: str, data: dict[str, str]) -> None:
        delivered.append((message_id, data))
        visible.set()

    recovery = DeferredTaskRecovery(
        redis_provider=lambda: redis,
        consumer_group=group,
        current_consumer_name=lambda: consumer,
        generation_guard=_current_generation,
        on_visible=on_visible,
        visibility_seconds=0,
        retry_seconds=0.01,
    )
    try:
        await redis.xgroup_create(stream_key, group, id="0", mkstream=True)
        deferred_id = await redis.xadd(stream_key, {"task_id": "deferred"})
        untouched_id = await redis.xadd(stream_key, {"task_id": "long-running"})
        await redis.xreadgroup(group, consumer, {stream_key: ">"}, count=2)
        await recovery.start()
        await recovery.defer(
            stream_key=stream_key,
            message_id=deferred_id,
            consumer_name=consumer,
        )

        await asyncio.wait_for(visible.wait(), timeout=1)

        deferred = await _pending(redis, stream_key, group, message_id=deferred_id)
        untouched = await _pending(redis, stream_key, group, message_id=untouched_id)
        assert delivered == [(deferred_id, {"task_id": "deferred"})]
        assert deferred["consumer"] == consumer
        assert deferred["times_delivered"] == 1
        assert untouched["consumer"] == consumer
        assert untouched["times_delivered"] == 1
    finally:
        await recovery.stop()
        await redis.delete(stream_key)
        await redis.aclose()


async def _current_generation() -> bool:
    return True


async def _pending(redis, stream_key: str, group: str, *, message_id: str) -> dict:
    result = await redis.xpending_range(
        stream_key,
        group,
        min=message_id,
        max=message_id,
        count=1,
    )
    assert len(result) == 1
    return result[0]
