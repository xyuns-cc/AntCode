"""Idempotent Redis cleanup for a deleted Crawl project."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from antcode_core.application.services.crawl.backends import redis_keys
from antcode_core.application.services.crawl.backends.redis_progress import RedisProgressStore
from antcode_core.infrastructure.redis.client import get_redis_client
from antcode_core.infrastructure.redis.control_plane import redis_namespace


@dataclass(frozen=True)
class CrawlProjectCleanupRequest:
    project_id: str
    batch_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.project_id.strip():
            raise ValueError("Crawl Redis 清理要求非空 project_id")
        if any(not batch_id.strip() for batch_id in self.batch_ids):
            raise ValueError("Crawl Redis 清理要求非空 batch_id")
        if len(set(self.batch_ids)) != len(self.batch_ids):
            raise ValueError("Crawl Redis 清理 batch_ids 不得重复")


@dataclass(frozen=True)
class CrawlProjectCleanupReport:
    project_id: str
    batch_count: int


class CrawlProjectRedisCleanup:
    def __init__(self, redis_client: Any | None = None, *, namespace: str | None = None) -> None:
        self._redis = redis_client
        self._namespace = redis_namespace(namespace)

    async def cleanup(self, request: CrawlProjectCleanupRequest) -> CrawlProjectCleanupReport:
        redis = await self._client()
        progress = RedisProgressStore(redis_client=redis, namespace=self._namespace)
        for batch_id in request.batch_ids:
            await progress.fence_and_clear(request.project_id, batch_id)
            await self._verify_batch(redis, request.project_id, batch_id)
        return CrawlProjectCleanupReport(
            project_id=request.project_id,
            batch_count=len(request.batch_ids),
        )

    async def _client(self) -> Any:
        if self._redis is None:
            self._redis = await get_redis_client()
        return self._redis

    async def _verify_batch(self, redis: Any, project_id: str, batch_id: str) -> None:
        active_keys = (
            redis_keys.crawl_progress_key(project_id, batch_id, self._namespace),
            redis_keys.crawl_checkpoint_key(project_id, batch_id, self._namespace),
            redis_keys.crawl_workers_key(project_id, batch_id, self._namespace),
        )
        fence = redis_keys.crawl_cancel_fence_key(project_id, batch_id, self._namespace)
        if int(await redis.exists(*active_keys)) != 0 or int(await redis.exists(fence)) != 1:
            raise RuntimeError(f"Crawl batch Redis 清理复核失败: project={project_id} batch={batch_id}")


crawl_project_redis_cleanup = CrawlProjectRedisCleanup()


__all__ = [
    "CrawlProjectCleanupReport",
    "CrawlProjectCleanupRequest",
    "CrawlProjectRedisCleanup",
    "crawl_project_redis_cleanup",
]
