"""Real Redis contracts for Crawl progress cancellation fences."""

from __future__ import annotations

import pytest
from antcode_core.application.services.crawl.backends.redis_progress import RedisProgressStore

from tests.integration.crawl.redis_live_support import (
    REDIS_REQUIRED_REASON,
    REDIS_URL,
    scoped_redis,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not REDIS_URL, reason=REDIS_REQUIRED_REASON),
]


@pytest.mark.asyncio
async def test_fence_and_clear_permanently_rejects_late_batch_writes() -> None:
    async with scoped_redis() as (redis, namespace):
        store = RedisProgressStore(redis_client=redis, namespace=namespace)
        project_id = "fenced-progress"
        batch_id = "batch-fenced"
        assert await store.set_progress(project_id, batch_id, {"processed": 1})
        assert await store.save_checkpoint(project_id, batch_id, {"offset": 1})
        assert await store.register_worker(project_id, batch_id, "worker-before", ttl=30)

        assert await store.fence_and_clear(project_id, batch_id)

        fence_key = store._get_cancel_fence_key(project_id, batch_id)
        assert await redis.get(fence_key) == b"1"
        assert await redis.ttl(fence_key) == -1
        assert await store.get_progress(project_id, batch_id) is None
        assert await store.load_checkpoint(project_id, batch_id) is None
        assert await store.get_active_workers(project_id, batch_id) == []
        assert not await store.set_progress(project_id, batch_id, {"processed": 2})
        assert not await store.update_progress(project_id, batch_id, {"failed": 1})
        assert not await store.save_checkpoint(project_id, batch_id, {"offset": 2})
        assert not await store.register_worker(project_id, batch_id, "worker-late", ttl=30)
        with pytest.raises(RuntimeError, match="已取消"):
            await store.increment_progress(project_id, batch_id, "processed")
        assert await redis.ttl(fence_key) == -1


@pytest.mark.asyncio
async def test_zero_ttl_worker_is_removed_using_redis_time() -> None:
    async with scoped_redis() as (redis, namespace):
        store = RedisProgressStore(redis_client=redis, namespace=namespace)
        project_id = "worker-expiry"
        batch_id = "batch-expiry"

        assert await store.register_worker(project_id, batch_id, "worker-zero", ttl=0)
        assert await store.get_active_workers(project_id, batch_id) == []
        assert await redis.zcard(store._get_workers_key(project_id, batch_id)) == 0
