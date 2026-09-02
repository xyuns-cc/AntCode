"""Idempotent Redis cleanup for a deleted Crawl project."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from antcode_core.application.services.crawl.backends import redis_keys
from antcode_core.application.services.crawl.backends.redis_progress import RedisProgressStore
from antcode_core.infrastructure.redis.client import get_redis_client
from antcode_core.infrastructure.redis.control_plane import redis_namespace

_FENCE_PROJECT_AND_CLEAR = """
redis.call('SET', KEYS[1], '1')
return redis.call('DEL', unpack(KEYS, 2))
"""


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
    project_keys_deleted: int
    project_fence_retained: bool
    cancel_fences_retained: int


class CrawlProjectRedisCleanup:
    def __init__(self, redis_client: Any | None = None, *, namespace: str | None = None) -> None:
        self._redis = redis_client
        self._namespace = redis_namespace(namespace)

    async def cleanup(self, request: CrawlProjectCleanupRequest) -> CrawlProjectCleanupReport:
        redis = await self._client()
        project_keys = self._project_keys(request.project_id)
        deleted = int(
            await redis.eval(
                _FENCE_PROJECT_AND_CLEAR,
                len(project_keys) + 1,
                redis_keys.crawl_project_deleted_key(request.project_id, self._namespace),
                *project_keys,
            )
        )
        progress = RedisProgressStore(redis_client=redis, namespace=self._namespace)
        for batch_id in request.batch_ids:
            await progress.fence_and_clear(request.project_id, batch_id)
        await self._verify(redis, request, project_keys)
        return CrawlProjectCleanupReport(
            project_id=request.project_id,
            batch_count=len(request.batch_ids),
            project_keys_deleted=deleted,
            project_fence_retained=True,
            cancel_fences_retained=len(request.batch_ids),
        )

    async def _client(self) -> Any:
        if self._redis is None:
            self._redis = await get_redis_client()
        return self._redis

    def _project_keys(self, project_id: str) -> tuple[str, ...]:
        return (redis_keys.crawl_dedup_key(project_id, self._namespace),)

    async def _verify(
        self,
        redis: Any,
        request: CrawlProjectCleanupRequest,
        project_keys: tuple[str, ...],
    ) -> None:
        if int(await redis.exists(*project_keys)) != 0:
            raise RuntimeError(f"Crawl 项目 Redis key 清理复核失败: project={request.project_id}")
        project_fence = redis_keys.crawl_project_deleted_key(request.project_id, self._namespace)
        if int(await redis.exists(project_fence)) != 1:
            raise RuntimeError(f"Crawl 项目删除 fence 复核失败: project={request.project_id}")
        for batch_id in request.batch_ids:
            await self._verify_batch(redis, request.project_id, batch_id)

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
