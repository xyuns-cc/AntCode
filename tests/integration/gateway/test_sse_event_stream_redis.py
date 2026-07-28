"""Live Redis contracts for the shared SSE event stream ledger."""

from __future__ import annotations

import asyncio
import os
import secrets
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
import redis.asyncio as aioredis
from antcode_core.infrastructure.redis import sse_event_stream as stream_module
from antcode_core.infrastructure.redis.sse_event_stream import decode_sse_event, publish_sse_event

REDIS_URL = os.getenv("ANTCODE_INTEGRATION_REDIS_URL")
pytestmark = pytest.mark.skipif(not REDIS_URL, reason="ANTCODE_INTEGRATION_REDIS_URL is required")


@pytest_asyncio.fixture
async def live_stream(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[object, tuple[str, ...]]]:
    namespace = f"antcode-sse-live-{secrets.token_hex(6)}"
    keys = stream_module._accounting_keys(namespace)
    monkeypatch.setattr(stream_module, "_accounting_keys", lambda: keys)
    redis = aioredis.from_url(REDIS_URL, decode_responses=False)
    await redis.delete(*keys)
    try:
        yield redis, keys
    finally:
        await redis.delete(*keys)
        await redis.aclose()


async def _assert_ledger_consistent(redis: object, keys: tuple[str, ...]) -> list[tuple[bytes, dict[bytes, bytes]]]:
    stream, total_key, order_key, sizes_key = keys
    entries = await redis.xrange(stream, "-", "+")
    order = await redis.lrange(order_key, 0, -1)
    sizes = await redis.hgetall(sizes_key)
    total = int(await redis.get(total_key) or 0)
    entry_ids = [entry_id for entry_id, _fields in entries]
    assert order == entry_ids
    assert set(sizes) == set(entry_ids)
    assert total == sum(int(size) for size in sizes.values())
    return entries


@pytest.mark.asyncio
async def test_concurrent_publish_keeps_all_four_ledger_keys_consistent(live_stream) -> None:
    redis, keys = live_stream
    messages = [
        {"type": "log_line", "run_id": "run-live", "data": {"sequence": index, "content": f"line-{index}"}}
        for index in range(100)
    ]

    await asyncio.gather(*(publish_sse_event(message, redis=redis) for message in messages))

    entries = await _assert_ledger_consistent(redis, keys)
    decoded = [decode_sse_event(fields) for _entry_id, fields in entries]
    assert {message["data"]["sequence"] for message in decoded} == set(range(100))


@pytest.mark.asyncio
async def test_length_trimming_removes_matching_order_and_size_entries(live_stream, monkeypatch) -> None:
    redis, keys = live_stream
    monkeypatch.setattr(stream_module, "SSE_EVENT_STREAM_MAXLEN", 3)

    for index in range(5):
        await publish_sse_event({"type": "run_status", "run_id": f"run-{index}"}, redis=redis)

    entries = await _assert_ledger_consistent(redis, keys)
    assert [decode_sse_event(fields)["run_id"] for _entry_id, fields in entries] == ["run-2", "run-3", "run-4"]


@pytest.mark.asyncio
async def test_corrupt_total_resets_atomically_without_reusing_stream_id(live_stream) -> None:
    redis, keys = live_stream
    stream, total_key, _order_key, _sizes_key = keys
    await publish_sse_event({"type": "run_status", "run_id": "before"}, redis=redis)
    old_id = (await redis.xrange(stream, "-", "+"))[0][0]
    await redis.set(total_key, -1)

    await publish_sse_event({"type": "run_status", "run_id": "after"}, redis=redis)

    entries = await _assert_ledger_consistent(redis, keys)
    assert len(entries) == 1
    assert decode_sse_event(entries[0][1])["run_id"] == "after"
    assert _stream_id(entries[0][0]) > _stream_id(old_id)


@pytest.mark.asyncio
async def test_missing_size_entry_resets_before_next_append(live_stream) -> None:
    redis, keys = live_stream
    _stream, _total_key, _order_key, sizes_key = keys
    await publish_sse_event({"type": "run_status", "run_id": "before"}, redis=redis)
    await redis.delete(sizes_key)

    await publish_sse_event({"type": "run_status", "run_id": "after"}, redis=redis)

    entries = await _assert_ledger_consistent(redis, keys)
    assert len(entries) == 1
    assert decode_sse_event(entries[0][1])["run_id"] == "after"


def _stream_id(raw: bytes) -> tuple[int, int]:
    milliseconds, sequence = raw.decode("ascii").split("-", 1)
    return int(milliseconds), int(sequence)
