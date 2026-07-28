"""Spider cleanup tombstone ordering and cluster-slot contract."""

from __future__ import annotations

import pytest
from antcode_core.application.services.crawl.spider_storage_cleanup import SpiderStorageCleanupService
from antcode_core.infrastructure.redis.keys import RedisKeys
from redis.cluster import key_slot


class _Pipeline:
    def __init__(self, redis: _Redis, phase: int) -> None:
        self._redis = redis
        self._phase = phase

    def eval(self, script: str, numkeys: int, *keys: str) -> None:
        assert numkeys == 5
        assert "SET" in script
        assert "EXPIRE" not in script
        assert len({key_slot(key.encode()) for key in keys}) == 1
        self._redis.tombstone_key = keys[0]
        self._redis.deleted_keys = keys[1:]

    def zrem(self, index_key: str, run_id: str) -> None:
        self._redis.index_removals.append((index_key, run_id))

    async def execute(self) -> None:
        self._redis.events.append("fence" if self._phase == 1 else "index_cleanup")


class _Redis:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.pipeline_count = 0
        self.tombstone_key = ""
        self.deleted_keys: tuple[str, ...] = ()
        self.index_removals: list[tuple[str, str]] = []

    def pipeline(self, *, transaction: bool) -> _Pipeline:
        assert transaction is False
        self.pipeline_count += 1
        return _Pipeline(self, self.pipeline_count)


@pytest.mark.asyncio
async def test_cleanup_fences_and_deletes_before_index_removal() -> None:
    redis = _Redis()

    await SpiderStorageCleanupService(redis, RedisKeys()).delete_runs(["run-1"], "project-1")

    assert redis.events == ["fence", "index_cleanup"]
    assert redis.tombstone_key == "{antcode}:spider:run-1:tombstone"
    assert redis.deleted_keys == (
        "{antcode}:spider:run-1:data",
        "{antcode}:spider:run-1:meta",
        "{antcode}:spider:run-1:item-ids",
        "{antcode}:spider:run-1:item-order",
    )
    assert redis.index_removals == [
        ("{antcode}:spider:index:project-1", "run-1"),
        ("{antcode}:spider:index:expiry:project-1", "run-1"),
    ]
