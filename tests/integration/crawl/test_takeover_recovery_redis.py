from __future__ import annotations

import os
import uuid

import pytest
import redis.asyncio as aioredis
from antcode_core.application.services.crawl.backends import QueueProjectDiscovery, QueueTask
from antcode_core.application.services.crawl.backends.redis_keys import (
    crawl_project_deleted_key,
)
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

    async def discover_projects(self) -> QueueProjectDiscovery:
        return QueueProjectDiscovery((self._project_id,))


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


@pytest.mark.asyncio
async def test_takeover_rejects_deleted_project_with_residual_pel() -> None:
    if not REDIS_URL:
        pytest.fail("ANTCODE_INTEGRATION_REDIS_URL is required")
    redis = aioredis.from_url(REDIS_URL, decode_responses=False)
    project_id = f"deleted-takeover-{uuid.uuid4().hex}"
    group = f"deleted-takeover-group-{uuid.uuid4().hex}"
    backend = RedisCrawlQueueBackend(
        stream_client=StreamClient(redis),
        consumer_group=group,
    )
    stream_keys = get_all_priority_keys(project_id)
    fence_key = crawl_project_deleted_key(project_id)
    keys = [*stream_keys, get_dead_letter_key(project_id), fence_key]

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
            [QueueTask(url="https://example.test/deleted")],
            Priority.NORMAL,
        )
        claimed = await backend.dequeue(project_id, "crashed-worker", count=1, timeout_ms=1)
        assert len(claimed) == 1
        assert await backend.get_pending_count(project_id) == 1
        await redis.set(fence_key, "1")

        report = await service.recover()

        assert report.projects_scanned == 0
        assert report.tasks_requeued == 0
        assert report.tasks_dead_lettered == 0
        assert len(report.failures) == 1
        assert "已删除 Crawl 项目仍存在 Stream" in report.failures[0]
        assert await backend.get_pending_count(project_id) == 1
        assert await redis.xlen(stream_keys[1]) == 1
    finally:
        await redis.delete(*keys)
        await redis.aclose()


@pytest.mark.asyncio
async def test_takeover_recovers_active_project_beside_fenced_residual() -> None:
    if not REDIS_URL:
        pytest.fail("ANTCODE_INTEGRATION_REDIS_URL is required")
    redis = aioredis.from_url(REDIS_URL, decode_responses=False)
    active_id = f"active-takeover-{uuid.uuid4().hex}"
    deleted_id = f"deleted-takeover-{uuid.uuid4().hex}"
    group = f"mixed-takeover-group-{uuid.uuid4().hex}"
    backend = RedisCrawlQueueBackend(stream_client=StreamClient(redis), consumer_group=group)
    active_keys = get_all_priority_keys(active_id)
    deleted_keys = get_all_priority_keys(deleted_id)
    fence_key = crawl_project_deleted_key(deleted_id)
    cleanup_keys = [
        *active_keys,
        *deleted_keys,
        get_dead_letter_key(active_id),
        get_dead_letter_key(deleted_id),
        fence_key,
    ]

    async def no_batches() -> list[str]:
        return []

    service = CrawlTakeoverRecoveryService(
        backend,
        config=TakeoverRecoveryConfig(timeout_ms=0, batch_size=10),
        batch_id_loader=no_batches,
    )
    try:
        for project_id in (active_id, deleted_id):
            await backend.ensure_queues(project_id)
            await backend.enqueue(project_id, [QueueTask(url=f"https://example.test/{project_id}")], Priority.NORMAL)
            assert len(await backend.dequeue(project_id, "crashed-worker", count=1, timeout_ms=1)) == 1
        await redis.set(fence_key, "1")

        report = await service.recover()

        assert report.projects_scanned == 1
        assert report.tasks_requeued == 1
        assert report.tasks_dead_lettered == 0
        assert report.failures == (f"已删除 Crawl 项目仍存在 Stream: project={deleted_id}",)
        assert await backend.get_pending_count(active_id) == 0
        assert await backend.get_pending_count(deleted_id) == 1
        assert await redis.xlen(deleted_keys[1]) == 1
    finally:
        await redis.delete(*cleanup_keys)
        await redis.aclose()
