"""Idempotent Redis cleanup for deleted Spider task runs.

本模块只依赖 Redis key 规范，刻意不导入 ORM 模型或控制面配置：
项目级联删除的清理路径要能在没有 DATABASE_URL 的进程里导入。
按状态过滤的常量见 ``antcode_core.domain.models.task_status_sets``。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from antcode_core.infrastructure.redis.keys import RedisKeys

_DELETE_BATCH_SIZE = 200
SPIDER_CLEANUP_EVENT_RUN_LIMIT = 200

_TOMBSTONE_AND_DELETE_LUA = r"""
redis.call('SET', KEYS[1], 'deleted')
return redis.call('DEL', KEYS[2], KEYS[3], KEYS[4], KEYS[5])
"""


def iter_cleanup_run_batches(run_ids: Sequence[str]) -> tuple[tuple[str, ...], ...]:
    normalized = tuple(dict.fromkeys(run_id.strip() for run_id in run_ids if run_id.strip()))
    return tuple(
        normalized[offset : offset + SPIDER_CLEANUP_EVENT_RUN_LIMIT]
        for offset in range(0, len(normalized), SPIDER_CLEANUP_EVENT_RUN_LIMIT)
    )


class SpiderStorageCleanupService:
    def __init__(self, redis_client: Any, keys: RedisKeys) -> None:
        self._redis = redis_client
        self._keys = keys

    async def delete_runs(self, run_ids: Sequence[str], project_id: str) -> None:
        normalized = tuple(dict.fromkeys(run_id.strip() for run_id in run_ids if run_id.strip()))
        if not normalized:
            return
        if not project_id or project_id != project_id.strip():
            raise ValueError("Spider cleanup project_id 必须非空且无首尾空白")
        for offset in range(0, len(normalized), _DELETE_BATCH_SIZE):
            await self._delete_batch(normalized[offset : offset + _DELETE_BATCH_SIZE], project_id)

    async def _delete_batch(self, run_ids: Sequence[str], project_id: str) -> None:
        pipeline = self._redis.pipeline(transaction=False)
        for run_id in run_ids:
            pipeline.eval(
                _TOMBSTONE_AND_DELETE_LUA,
                5,
                self._keys.spider_tombstone_key(run_id),
                self._keys.spider_data_stream(run_id),
                self._keys.spider_meta_key(run_id),
                self._keys.spider_item_ids_key(run_id),
                self._keys.spider_item_order_key(run_id),
            )
        await pipeline.execute()

        pipeline = self._redis.pipeline(transaction=False)
        index_key = self._keys.spider_index_key(project_id)
        index_expiry_key = self._keys.spider_index_expiry_key(project_id)
        for run_id in run_ids:
            pipeline.zrem(index_key, run_id)
            pipeline.zrem(index_expiry_key, run_id)
        await pipeline.execute()


__all__ = [
    "SPIDER_CLEANUP_EVENT_RUN_LIMIT",
    "SpiderStorageCleanupService",
    "iter_cleanup_run_batches",
]
