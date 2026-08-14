"""Atomic Crawl Redis queue behavior contracts."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.crawl.backends.base import QueueTask
from antcode_core.application.services.crawl.backends.redis_queue import RedisCrawlQueueBackend
from antcode_core.application.services.crawl.backends.redis_queue_payloads import (
    dead_letter_payload,
    invalid_message_payload,
)
from antcode_core.domain.models.enums import Priority
from antcode_core.infrastructure.redis.stream_client import StreamClient, StreamMessage

DLQ_TTL_SECONDS = 604_800


def _task(url: str = "https://example.test") -> QueueTask:
    return QueueTask(url=url, batch_id="batch-1", project_id="project-1", priority=Priority.HIGH)


@pytest.mark.asyncio
async def test_xadd_unique_uses_same_slot_lua_and_decodes_id() -> None:
    redis = AsyncMock()
    redis.eval.return_value = b"1-0"
    client = StreamClient(redis)
    stream_key = "{tenant:crawl:project-1}:stream:0"
    dedup_key = "{tenant:crawl:project-1}:dedup"

    result = await client.xadd_unique(
        stream_key,
        dedup_key,
        deleted_fence_key="{tenant:crawl:project-1}:deleted",
        fingerprint="fingerprint",
        data=_task().to_dict(),
    )

    assert result == "1-0"
    args = redis.eval.await_args.args
    assert args[1:5] == (3, stream_key, dedup_key, "{tenant:crawl:project-1}:deleted")
    assert args[5] == "fingerprint"


@pytest.mark.asyncio
async def test_xadd_unique_duplicate_returns_none() -> None:
    redis = AsyncMock()
    redis.eval.return_value = False

    assert (
        await StreamClient(redis).xadd_unique(
            "{p}:stream",
            "{p}:dedup",
            deleted_fence_key="{p}:deleted",
            fingerprint="fp",
            data=_task().to_dict(),
        )
        is None
    )


@pytest.mark.asyncio
async def test_xack_delete_only_uses_atomic_pel_script() -> None:
    redis = AsyncMock()
    redis.eval.return_value = 1
    client = StreamClient(redis)

    assert await client.xack_delete("{p}:stream", ["1-0", "2-0"], group_name="group") == 1

    args = redis.eval.await_args.args
    assert args[1:] == (1, "{p}:stream", "group", "1-0", "2-0")
    redis.xack.assert_not_awaited()
    redis.xdel.assert_not_awaited()


@pytest.mark.asyncio
async def test_move_pending_forwards_dlq_ttl_and_payload() -> None:
    redis = AsyncMock()
    redis.eval.return_value = b"2-0"
    client = StreamClient(redis)

    result = await client.move_pending(
        "{p}:source",
        "{p}:dlq",
        group_name="group",
        msg_id="1-0",
        data={"status": "failed"},
        maxlen=100,
        expire_seconds=DLQ_TTL_SECONDS,
        deleted_fence_key="{p}:deleted",
    )

    assert result == "2-0"
    assert redis.eval.await_args.args[1:10] == (
        3,
        "{p}:source",
        "{p}:dlq",
        "{p}:deleted",
        "group",
        "1-0",
        100,
        DLQ_TTL_SECONDS,
        "status",
    )


@pytest.mark.asyncio
async def test_dequeue_quarantines_invalid_item_and_returns_later_valid_item() -> None:
    client = AsyncMock(spec=StreamClient)
    client.xreadgroup.side_effect = [
        [
            StreamMessage("1-0", {"url": "", "headers": {}}),
            StreamMessage("2-0", _task("https://valid.test").to_dict()),
        ],
        [],
        [],
    ]
    client.move_pending.return_value = "3-0"
    backend = RedisCrawlQueueBackend(stream_client=client, namespace="tenant")

    tasks = await backend.dequeue("project-1", "worker-1", count=2, timeout_ms=0)

    assert [task.url for task in tasks] == ["https://valid.test"]
    move = client.move_pending.await_args.kwargs
    assert move["msg_id"] == "1-0"
    assert move["expire_seconds"] == DLQ_TTL_SECONDS
    assert move["deleted_fence_key"] == "{tenant:crawl:project-1}:deleted"
    assert "headers" not in move["data"]


@pytest.mark.asyncio
async def test_reclaim_advances_xautoclaim_cursor_across_pages() -> None:
    client = AsyncMock(spec=StreamClient)
    client.xautoclaim.side_effect = [
        ("5-0", [StreamMessage("1-0", _task("https://one.test").to_dict())], []),
        ("0-0", [StreamMessage("2-0", _task("https://two.test").to_dict())], []),
    ]
    client.xpending_range.return_value = []
    backend = RedisCrawlQueueBackend(stream_client=client, namespace="tenant")

    reclaimed = await backend.reclaim("project-1", min_idle_ms=0, count=2)

    assert [item.task.url for item in reclaimed] == ["https://one.test", "https://two.test"]
    assert [call.kwargs["start_id"] for call in client.xautoclaim.await_args_list] == ["0-0", "5-0"]


@pytest.mark.asyncio
async def test_queue_stats_do_not_double_count_pel_entries() -> None:
    client = AsyncMock(spec=StreamClient)
    stream_lengths = (5, 4, 1)
    pending_counts = (2, 1, 3)
    dead_letter_count = 2
    client.xlen.side_effect = [*stream_lengths, dead_letter_count]
    client.xpending.side_effect = [
        {"pending_count": pending_counts[0], "consumers": {"one": pending_counts[0]}},
        {"pending_count": pending_counts[1], "consumers": {"two": pending_counts[1]}},
        {"pending_count": pending_counts[2], "consumers": {"three": pending_counts[2]}},
    ]
    backend = RedisCrawlQueueBackend(stream_client=client, namespace="tenant")

    stats = await backend.stats("project-1")
    available_counts = tuple(max(length - pending, 0) for length, pending in zip(stream_lengths, pending_counts))

    assert stats.pending == sum(available_counts)
    assert stats.processing == sum(pending_counts)
    assert stats.total == sum(available_counts) + sum(pending_counts)
    assert stats.dead_letter == dead_letter_count


def test_dead_letter_payload_redacts_headers_without_mutating_task() -> None:
    headers = {
        "Authorization": "bearer secret",
        "COOKIE": "session=secret",
        "Proxy-Authorization": "secret",
        "Set-Cookie": "secret",
        "X-API-Key": "secret",
        "api-Key": "secret",
        "Accept": "application/json",
    }
    task = QueueTask(url="https://example.test", headers=headers, priority=Priority.NORMAL)

    payload = dead_letter_payload(task, "failed")

    assert payload["headers"] == {"Accept": "application/json"}
    assert task.headers == headers


def test_invalid_message_payload_does_not_copy_untrusted_headers() -> None:
    payload = invalid_message_payload(
        "1-0",
        {"url": "https://example.test", "headers": {"Authorization": "secret"}, "extra": "secret"},
        ValueError("invalid"),
    )

    assert "headers" not in payload
    assert "extra" not in payload


@pytest.mark.parametrize(
    "data",
    [
        {"url": "https://example.test", "headers": []},
        {"url": "https://example.test", "depth": 1.5},
        {"url": "https://example.test", "parent_url": []},
    ],
)
def test_queue_task_rejects_lossy_input_coercion(data: dict) -> None:
    with pytest.raises(ValueError):
        QueueTask.from_dict(data)
