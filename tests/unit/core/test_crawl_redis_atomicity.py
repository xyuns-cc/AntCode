"""Crawl Redis namespace, Cluster slot, and checkpoint atomicity contracts."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.crawl.backends.redis_dedup import (
    RedisDedupStore,
)
from antcode_core.application.services.crawl.backends.redis_keys import (
    crawl_batch_workers_key,
    crawl_test_result_key,
    crawl_worker_registry_key,
)
from antcode_core.application.services.crawl.backends.redis_progress import (
    RedisProgressStore,
)
from antcode_core.application.services.crawl.backends.redis_progress_codec import decode_hash
from antcode_core.application.services.crawl.backends.redis_queue import (
    RedisCrawlQueueBackend,
    get_all_priority_keys,
    get_dead_letter_key,
)
from antcode_core.application.services.crawl.progress_service import CrawlProgressService
from antcode_core.domain.models.enums import Priority

PROGRESS_KEY_COUNT = 3
FENCED_WRITE_KEY_COUNT = 2


def _hash_tag(key: str) -> str:
    return key[key.index("{") + 1 : key.index("}")]


def test_progress_hash_decode_rejects_corrupt_json() -> None:
    with pytest.raises(Exception):
        decode_hash({b"completed_urls": b"not-json"})


def test_crawl_keys_include_namespace_and_share_aggregate_slots() -> None:
    queue_keys = [*get_all_priority_keys("project-1", "tenant-a"), get_dead_letter_key("project-1", "tenant-a")]
    progress = RedisProgressStore(redis_client=AsyncMock(), namespace="tenant-a")
    progress_keys = [
        progress._get_progress_key("project-1", "batch-1"),
        progress._get_checkpoint_key("project-1", "batch-1"),
        progress._get_workers_key("project-1", "batch-1"),
    ]

    assert {_hash_tag(key) for key in queue_keys} == {"tenant-a:crawl:project-1"}
    assert {_hash_tag(key) for key in progress_keys} == {"tenant-a:crawl:project-1:batch-1"}
    assert (
        RedisDedupStore(AsyncMock(), namespace="tenant-a")._key("project-1").startswith("{tenant-a:crawl:project-1}:")
    )
    assert get_dead_letter_key("project-1", "tenant-b") != queue_keys[-1]
    assert crawl_worker_registry_key("tenant-a") == "tenant-a:crawl:workers:registry"
    assert crawl_batch_workers_key("batch-1", "tenant-a") == "tenant-a:crawl:batch:batch-1:workers"
    assert crawl_test_result_key("batch-1", "tenant-a") == "tenant-a:crawl:test:result:batch-1"


@pytest.mark.asyncio
async def test_checkpoint_replacement_is_one_atomic_eval() -> None:
    redis = AsyncMock()
    redis.eval.return_value = 1
    store = RedisProgressStore(redis_client=redis, namespace="tenant-a")

    assert await store.save_checkpoint("project-1", "batch-1", {"offset": 7, "done": False})

    args = redis.eval.await_args.args
    assert args[1] == FENCED_WRITE_KEY_COUNT
    assert args[2] == "{tenant-a:crawl:project-1:batch-1}:checkpoint"
    assert args[3] == "{tenant-a:crawl:project-1:batch-1}:cancelled"
    assert args[4:] == ("offset", "7", "done", "false")
    redis.delete.assert_not_awaited()
    redis.hset.assert_not_awaited()


@pytest.mark.asyncio
async def test_progress_clear_uses_one_cluster_slot_delete() -> None:
    redis = AsyncMock()
    redis.delete.return_value = 3
    store = RedisProgressStore(redis_client=redis, namespace="tenant-a")

    assert await store.clear("project-1", "batch-1")

    keys = redis.delete.await_args.args
    assert len(keys) == PROGRESS_KEY_COUNT
    assert {_hash_tag(key) for key in keys} == {"tenant-a:crawl:project-1:batch-1"}


@pytest.mark.asyncio
async def test_worker_registration_is_cancel_fenced_in_same_slot() -> None:
    redis = AsyncMock()
    redis.eval.return_value = 0
    store = RedisProgressStore(redis_client=redis, namespace="tenant-a")

    assert await store.register_worker("project-1", "batch-1", "worker-1", ttl=30) is False

    args = redis.eval.await_args.args
    assert args[1] == FENCED_WRITE_KEY_COUNT
    assert args[2] == "{tenant-a:crawl:project-1:batch-1}:workers"
    assert args[3] == "{tenant-a:crawl:project-1:batch-1}:cancelled"
    assert args[4:] == ("worker-1", 30000)


@pytest.mark.asyncio
async def test_progress_service_exposes_cancel_fence_rejection() -> None:
    backend = AsyncMock()
    backend.set_progress.return_value = False
    service = CrawlProgressService(backend=backend)

    with pytest.raises(RuntimeError, match="fence"):
        await service.init_progress("project-1", "batch-1", total_urls=1)


@pytest.mark.asyncio
async def test_queue_scan_round_trips_namespaced_project_id() -> None:
    stream_client = AsyncMock()
    stream_client.scan_keys.return_value = ["{tenant-a:crawl:project:segment}:stream:5"]
    stream_client.exists.return_value = False
    backend = RedisCrawlQueueBackend(stream_client=stream_client, namespace="tenant-a")

    assert await backend.list_project_ids() == ["project:segment"]
    stream_client.scan_keys.assert_awaited_once_with("{tenant-a:crawl:*}:stream:*")


@pytest.mark.asyncio
async def test_queue_scan_rejects_deleted_project_with_residual_stream() -> None:
    stream_client = AsyncMock()
    stream_client.scan_keys.return_value = ["{tenant-a:crawl:project-1}:stream:5"]
    stream_client.exists.return_value = True
    backend = RedisCrawlQueueBackend(stream_client=stream_client, namespace="tenant-a")

    with pytest.raises(RuntimeError, match="已删除 Crawl 项目仍存在 Stream"):
        await backend.list_project_ids()

    stream_client.exists.assert_awaited_once_with("{tenant-a:crawl:project-1}:deleted")


@pytest.mark.asyncio
async def test_queue_discovery_isolates_fenced_residual_from_active_project() -> None:
    stream_client = AsyncMock()
    stream_client.scan_keys.return_value = [
        "{tenant-a:crawl:active}:stream:5",
        "{tenant-a:crawl:deleted}:stream:5",
    ]
    stream_client.exists.side_effect = [False, True]
    backend = RedisCrawlQueueBackend(stream_client=stream_client, namespace="tenant-a")

    discovery = await backend.discover_projects()

    assert discovery.project_ids == ("active",)
    assert discovery.failures == ("已删除 Crawl 项目仍存在 Stream: project=deleted",)


@pytest.mark.asyncio
async def test_ack_only_settles_the_explicit_priority_stream() -> None:
    stream_client = AsyncMock()
    stream_client.xack_delete.return_value = 0
    events: list[tuple[str, str]] = []

    async def ensure(key: str, _group: str, **_kwargs) -> None:
        events.append(("ensure", key))

    async def ack_delete(key: str, _ids: list[str], *, group_name: str) -> int:
        assert group_name == "crawl_workers"
        events.append(("ack", key))
        return 0

    stream_client.ensure_active_group.side_effect = ensure
    stream_client.xack_delete.side_effect = ack_delete
    backend = RedisCrawlQueueBackend(stream_client=stream_client, namespace="tenant-a")

    assert await backend.ack("project-1", ["1-0"], Priority.HIGH) == 0
    assert [kind for kind, _key in events] == ["ensure", "ack"]
    assert {key for _kind, key in events} == {"{tenant-a:crawl:project-1}:stream:0"}


@pytest.mark.asyncio
async def test_reclaim_preflights_every_priority_group_before_claim() -> None:
    stream_client = AsyncMock()
    events: list[tuple[str, str]] = []

    async def ensure(key: str, _group: str, **_kwargs) -> None:
        events.append(("ensure", key))

    async def claim(key: str, **_kwargs):
        events.append(("claim", key))
        return "0-0", [], []

    stream_client.ensure_active_group.side_effect = ensure
    stream_client.xautoclaim.side_effect = claim
    backend = RedisCrawlQueueBackend(stream_client=stream_client, namespace="tenant-a")

    assert await backend.reclaim("project-1", min_idle_ms=0) == []
    assert [kind for kind, _key in events] == ["ensure", "ensure", "ensure", "claim", "claim", "claim"]
