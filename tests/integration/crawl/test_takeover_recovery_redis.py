from __future__ import annotations

import os
import uuid

import pytest
import redis.asyncio as aioredis
from antcode_core.application.services.crawl.backends import QueueTask
from antcode_core.application.services.crawl.backends.redis_queue import (
    RedisCrawlQueueBackend,
    get_all_priority_keys,
    get_dead_letter_key,
)
from antcode_core.application.services.crawl.takeover_recovery_service import (
    CrawlTakeoverRecoveryService,
    TakeoverRecoveryConfig,
)
from antcode_core.domain.models.enums import Priority
from antcode_core.infrastructure.redis.stream_client import StreamClient

REDIS_URL = os.getenv("ANTCODE_INTEGRATION_REDIS_URL")


class _ScopedRedisBackend(RedisCrawlQueueBackend):
    def __init__(self, project_id: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._project_id = project_id

    async def list_project_ids(self) -> list[str]:
        return [self._project_id]


@pytest.mark.asyncio
async def test_takeover_recovery_atomically_requeues_real_pel() -> None:
    if not REDIS_URL:
        pytest.fail("ANTCODE_INTEGRATION_REDIS_URL is required")
    redis = aioredis.from_url(REDIS_URL, decode_responses=False)
    project_id = f"takeover-{uuid.uuid4().hex}"
    group = f"takeover-group-{uuid.uuid4().hex}"
    backend = _ScopedRedisBackend(
        project_id,
        stream_client=StreamClient(redis),
        consumer_group=group,
    )
    keys = [*get_all_priority_keys(project_id), get_dead_letter_key(project_id)]

    async def no_batches() -> list[str]:
        return []

    service = CrawlTakeoverRecoveryService(
        backend,
        config=TakeoverRecoveryConfig(timeout_ms=0, batch_size=10),
        batch_id_loader=no_batches,
    )
    try:
        await backend.ensure_queues(project_id)
        await backend.enqueue(
            project_id,
            [QueueTask(url="https://example.test/recover")],
            Priority.NORMAL,
        )
        claimed = await backend.dequeue(project_id, "crashed-worker", count=1, timeout_ms=1)
        assert len(claimed) == 1
        assert await backend.get_pending_count(project_id) == 1

        first = await service.recover()
        stream_length = await redis.xlen(get_all_priority_keys(project_id)[1])
        second = await service.recover()

        assert first.tasks_requeued == 1
        assert await backend.get_pending_count(project_id) == 0
        assert second.tasks_requeued == 0
        assert await redis.xlen(get_all_priority_keys(project_id)[1]) == stream_length

        redelivered = await backend.dequeue(project_id, "replacement-worker", count=1, timeout_ms=1)
        assert len(redelivered) == 1
        assert redelivered[0].url == "https://example.test/recover"
    finally:
        await redis.delete(*keys)
        await redis.aclose()
