from __future__ import annotations

import pytest
from antcode_core.application.services.crawl.project_redis_cleanup import (
    CrawlProjectCleanupRequest,
    CrawlProjectRedisCleanup,
)


class _CleanupRedis:
    def __init__(self) -> None:
        self.keys: set[str] = set()
        self.eval_calls: list[tuple] = []

    async def eval(self, script: str, _key_count: int, *args) -> int:
        del script
        self.eval_calls.append(args)
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
            "{tenant:crawl:project-1:batch-1}:progress",
            "{tenant:crawl:project-1:batch-1}:checkpoint",
            "{tenant:crawl:project-1:batch-1}:workers",
        }
    )


@pytest.mark.asyncio
async def test_project_cleanup_clears_batch_state_and_retains_cancel_fence() -> None:
    redis = _CleanupRedis()
    _seed(redis)
    cleanup = CrawlProjectRedisCleanup(redis, namespace="tenant")
    request = CrawlProjectCleanupRequest("project-1", ("batch-1",))

    report = await cleanup.cleanup(request)

    assert report.batch_count == 1
    assert redis.keys == {"{tenant:crawl:project-1:batch-1}:cancelled"}


@pytest.mark.asyncio
async def test_project_cleanup_is_idempotent() -> None:
    redis = _CleanupRedis()
    _seed(redis)
    cleanup = CrawlProjectRedisCleanup(redis, namespace="tenant")
    request = CrawlProjectCleanupRequest("project-1", ("batch-1",))

    first = await cleanup.cleanup(request)
    second = await cleanup.cleanup(request)

    assert first.batch_count == 1
    assert second.batch_count == 1
    assert redis.keys == {"{tenant:crawl:project-1:batch-1}:cancelled"}


@pytest.mark.asyncio
async def test_project_cleanup_exposes_verification_failure() -> None:
    class BrokenRedis(_CleanupRedis):
        async def exists(self, *keys: str) -> int:
            if any(key.endswith(":progress") for key in keys):
                return 1
            return await super().exists(*keys)

    with pytest.raises(RuntimeError, match="复核失败"):
        await CrawlProjectRedisCleanup(BrokenRedis(), namespace="tenant").cleanup(
            CrawlProjectCleanupRequest("project-1", ("batch-1",))
        )


def test_cleanup_request_rejects_duplicate_batch_ids() -> None:
    with pytest.raises(ValueError, match="不得重复"):
        CrawlProjectCleanupRequest("project-1", ("batch-1", "batch-1"))
