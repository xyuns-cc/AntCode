from __future__ import annotations

import pytest
from antcode_core.application.services.crawl.project_redis_cleanup import (
    CrawlProjectCleanupRequest,
    CrawlProjectRedisCleanup,
)

PROJECT_KEY_COUNT = 1


class _CleanupRedis:
    def __init__(self) -> None:
        self.keys: set[str] = set()
        self.eval_calls: list[tuple] = []
        self.delete_calls: list[tuple[str, ...]] = []

    async def delete(self, *keys: str) -> int:
        self.delete_calls.append(keys)
        deleted = sum(key in self.keys for key in keys)
        self.keys.difference_update(keys)
        return deleted

    async def eval(self, script: str, _key_count: int, *args) -> int:
        self.eval_calls.append(args)
        if "unpack(KEYS, 2)" in script:
            fence, *project_keys = args
            self.keys.add(fence)
            deleted = sum(key in self.keys for key in project_keys)
            self.keys.difference_update(project_keys)
            return deleted
        progress, checkpoint, workers, fence = args
        self.keys.add(fence)
        deleted = sum(key in self.keys for key in (progress, checkpoint, workers))
        self.keys.difference_update((progress, checkpoint, workers))
        return deleted

    async def exists(self, *keys: str) -> int:
        return sum(key in self.keys for key in keys)


def _seed(redis: _CleanupRedis) -> None:
    redis.keys.update(
        {
            "{tenant:crawl:project-1}:dedup",
            "{tenant:crawl:project-1:batch-1}:progress",
            "{tenant:crawl:project-1:batch-1}:checkpoint",
            "{tenant:crawl:project-1:batch-1}:workers",
        }
    )


@pytest.mark.asyncio
async def test_project_cleanup_deletes_dedup_state_and_retains_batch_fence() -> None:
    redis = _CleanupRedis()
    _seed(redis)
    cleanup = CrawlProjectRedisCleanup(redis, namespace="tenant")
    request = CrawlProjectCleanupRequest("project-1", ("batch-1",))

    report = await cleanup.cleanup(request)

    assert report.project_keys_deleted == PROJECT_KEY_COUNT
    assert report.project_fence_retained is True
    assert report.cancel_fences_retained == 1
    assert redis.keys == {
        "{tenant:crawl:project-1}:deleted",
        "{tenant:crawl:project-1:batch-1}:cancelled",
    }


@pytest.mark.asyncio
async def test_project_cleanup_is_idempotent() -> None:
    redis = _CleanupRedis()
    _seed(redis)
    cleanup = CrawlProjectRedisCleanup(redis, namespace="tenant")
    request = CrawlProjectCleanupRequest("project-1", ("batch-1",))

    first = await cleanup.cleanup(request)
    second = await cleanup.cleanup(request)

    assert first.project_keys_deleted == PROJECT_KEY_COUNT
    assert second.project_keys_deleted == 0
    assert second.cancel_fences_retained == 1


@pytest.mark.asyncio
async def test_project_cleanup_exposes_verification_failure() -> None:
    class BrokenRedis(_CleanupRedis):
        async def exists(self, *keys: str) -> int:
            if any(key.endswith(":dedup") for key in keys):
                return 1
            return await super().exists(*keys)

    with pytest.raises(RuntimeError, match="复核失败"):
        await CrawlProjectRedisCleanup(BrokenRedis(), namespace="tenant").cleanup(
            CrawlProjectCleanupRequest("project-1")
        )


def test_cleanup_request_rejects_duplicate_batch_ids() -> None:
    with pytest.raises(ValueError, match="不得重复"):
        CrawlProjectCleanupRequest("project-1", ("batch-1", "batch-1"))
