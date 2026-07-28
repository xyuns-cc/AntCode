"""Bounded two-pass ingest history contracts."""

import gc
from types import SimpleNamespace
from unittest.mock import AsyncMock

import antcode_web_api.streams.ingest_history as history_module
import pytest
from antcode_web_api.streams.ingest_history import IngestLogHistoryReader


async def _checkpoint() -> None:
    return None


def _entry(
    sequence: int,
    *,
    content: str | None = None,
    storage_id: int | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        log_type="stdout",
        content=content or str(sequence),
        timestamp=None,
        sequence=sequence,
        event_id=f"42-0:{sequence}",
        storage_id=storage_id if storage_id is not None else sequence,
    )


class _TrackedPage(list):
    live_pages = 0
    max_live_pages = 0
    max_page_size = 0

    def __init__(self, values=()):
        super().__init__(values)
        type(self).live_pages += 1
        type(self).max_live_pages = max(type(self).max_live_pages, type(self).live_pages)
        type(self).max_page_size = max(type(self).max_page_size, len(self))

    def __del__(self):
        type(self).live_pages -= 1

    @classmethod
    def reset(cls) -> None:
        cls.live_pages = 0
        cls.max_live_pages = 0
        cls.max_page_size = 0


class _PagedPostgres:
    def __init__(self, total: int) -> None:
        self.total = total
        self.latest_calls = 0
        self.window_calls = 0

    async def latest(self, _run_id, *, limit, snapshot_id, before=None):
        self.latest_calls += 1
        top = before - 1 if before else min(snapshot_id, self.total)
        bottom = max(1, top - limit + 1)
        return _TrackedPage(_entry(sequence) for sequence in range(top, bottom - 1, -1))

    async def window(self, _run_id, *, limit, snapshot_id, lower, after=None):
        self.window_calls += 1
        start = after + 1 if after else lower
        stop = min(snapshot_id, start + limit - 1)
        return _TrackedPage(_entry(sequence) for sequence in range(start, stop + 1))


class _PagedRedis:
    def __init__(self, total: int) -> None:
        self.total = total
        self.reverse_calls = 0
        self.forward_calls = 0

    async def xrevrange(self, _key, *, max, min, count):
        self.reverse_calls += 1
        top = self.total if max == "+" else _stream_number(max)
        if max.startswith("("):
            top -= 1
        bottom = max_value(1, top - count + 1)
        return _TrackedPage(_redis_message(value) for value in range(top, bottom - 1, -1))

    async def xrange(self, _key, *, min, max, count):
        self.forward_calls += 1
        start = _stream_number(min)
        if min.startswith("("):
            start += 1
        stop = min_value(_stream_number(max), start + count - 1)
        return _TrackedPage(_redis_message(value) for value in range(start, stop + 1))


def _stream_number(stream_id: str) -> int:
    return int(stream_id.removeprefix("(").split("-", maxsplit=1)[0])


def _redis_message(sequence: int):
    return f"{sequence}-0".encode(), {"content": str(sequence)}


def min_value(left: int, right: int) -> int:
    return left if left < right else right


def max_value(left: int, right: int) -> int:
    return left if left > right else right


def _decode_legacy(fields, run_id_filter=None):
    sequence = int(fields["content"])
    return [{"log_type": "stdout", "content": str(sequence), "timestamp": "", "sequence": sequence}]


@pytest.mark.asyncio
async def test_postgres_history_normalizes_and_replays_oldest_first(monkeypatch):
    first = _entry(100, content="first", storage_id=1)
    second = _entry(11, content="second", storage_id=2)
    monkeypatch.setattr(history_module.postgres_log_service, "latest_snapshot_id", AsyncMock(return_value=2))
    monkeypatch.setattr(
        history_module.postgres_log_service, "list_latest_page", AsyncMock(return_value=[second, first])
    )
    monkeypatch.setattr(
        history_module.postgres_log_service,
        "list_history_window_page",
        AsyncMock(return_value=[first, second]),
    )
    reader = IngestLogHistoryReader("ac")

    window = await reader.plan_latest(
        "run-1", max_lines=10, max_bytes=100, size_of=lambda item: 1, checkpoint=_checkpoint
    )
    history = [item async for item in reader.iter_window(window, checkpoint=_checkpoint)]

    assert [item["sequence"] for item in history] == [100, 11]
    assert [item["storage_id"] for item in history] == [1, 2]
    assert [item["content"] for item in history] == ["first", "second"]
    assert all(item["source"] == "pg_history" for item in history)
    assert window.truncated is False


@pytest.mark.asyncio
async def test_postgres_invalid_timestamp_fails_explicitly(monkeypatch):
    entry = _entry(1)
    entry.timestamp = "not-a-datetime"
    monkeypatch.setattr(history_module.postgres_log_service, "latest_snapshot_id", AsyncMock(return_value=1))
    monkeypatch.setattr(history_module.postgres_log_service, "list_latest_page", AsyncMock(return_value=[entry]))

    with pytest.raises(AttributeError, match="isoformat"):
        await IngestLogHistoryReader("ac").plan_latest(
            "run-1",
            max_lines=10,
            max_bytes=100,
            size_of=lambda item: 1,
            checkpoint=_checkpoint,
        )


@pytest.mark.asyncio
async def test_postgres_large_history_keeps_only_bounded_pages(monkeypatch):
    _TrackedPage.reset()
    monkeypatch.setattr(history_module, "HISTORY_PAGE_SIZE", 17)
    monkeypatch.setattr(history_module, "HISTORY_FETCH_CHUNK_ROWS", 17)
    pages = _PagedPostgres(total=1001)
    monkeypatch.setattr(history_module.postgres_log_service, "latest_snapshot_id", AsyncMock(return_value=1001))
    monkeypatch.setattr(history_module.postgres_log_service, "list_latest_page", pages.latest)
    monkeypatch.setattr(history_module.postgres_log_service, "list_history_window_page", pages.window)
    reader = IngestLogHistoryReader("ac")

    window = await reader.plan_latest(
        "run-1", max_lines=1000, max_bytes=10_000, size_of=lambda item: 1, checkpoint=_checkpoint
    )
    count = 0
    previous = 1
    async for item in reader.iter_window(window, checkpoint=_checkpoint):
        assert item["sequence"] == previous + 1
        previous = item["sequence"]
        count += 1
    gc.collect()

    assert (count, previous, window.truncated) == (1000, 1001, True)
    assert pages.latest_calls > 50 and pages.window_calls > 50
    assert _TrackedPage.max_page_size <= 17
    assert _TrackedPage.max_live_pages <= 2
    assert _TrackedPage.live_pages == 0


@pytest.mark.asyncio
async def test_byte_budget_selects_latest_postgres_frame(monkeypatch):
    newest = _entry(3, content="123456")
    older = _entry(2, content="1234")
    monkeypatch.setattr(history_module.postgres_log_service, "latest_snapshot_id", AsyncMock(return_value=3))
    monkeypatch.setattr(
        history_module.postgres_log_service,
        "list_latest_page",
        AsyncMock(return_value=[newest, older]),
    )
    monkeypatch.setattr(
        history_module.postgres_log_service,
        "list_history_window_page",
        AsyncMock(return_value=[newest]),
    )
    reader = IngestLogHistoryReader("ac")

    window = await reader.plan_latest(
        "run-1",
        max_lines=10,
        max_bytes=6,
        size_of=lambda item: len(item["content"].encode()),
        checkpoint=_checkpoint,
    )
    history = [item async for item in reader.iter_window(window, checkpoint=_checkpoint)]

    assert [item["sequence"] for item in history] == [3]
    assert (window.sent_lines, window.total_bytes, window.truncated) == (1, 6, True)


@pytest.mark.asyncio
async def test_legacy_large_history_is_bounded_and_oldest_first(monkeypatch):
    _TrackedPage.reset()
    monkeypatch.setattr(history_module, "HISTORY_PAGE_SIZE", 13)
    monkeypatch.setattr(history_module, "HISTORY_FETCH_CHUNK_ROWS", 13)
    monkeypatch.setattr(history_module.postgres_log_service, "latest_snapshot_id", AsyncMock(return_value=0))
    redis = _PagedRedis(total=501)
    monkeypatch.setattr(history_module, "get_redis_client", AsyncMock(return_value=redis))
    monkeypatch.setattr(history_module, "decode_batch", _decode_legacy)
    reader = IngestLogHistoryReader("ac")

    window = await reader.plan_latest(
        "run-1", max_lines=500, max_bytes=10_000, size_of=lambda item: 1, checkpoint=_checkpoint
    )
    count = 0
    previous = 1
    async for item in reader.iter_window(window, checkpoint=_checkpoint):
        assert item["sequence"] == previous + 1
        previous = item["sequence"]
        count += 1
    gc.collect()

    assert (count, previous) == (500, 501)
    assert window.truncated is True
    assert redis.reverse_calls > 30 and redis.forward_calls > 30
    assert _TrackedPage.max_page_size <= 13
    assert _TrackedPage.max_live_pages <= 2


@pytest.mark.asyncio
async def test_legacy_unavailable_is_explicit(monkeypatch):
    monkeypatch.setattr(history_module.postgres_log_service, "latest_snapshot_id", AsyncMock(return_value=0))
    monkeypatch.setattr(history_module, "get_redis_client", AsyncMock(return_value=None))

    with pytest.raises(RuntimeError, match="Redis 客户端不可用"):
        await IngestLogHistoryReader("ac").plan_latest(
            "run-1",
            max_lines=10,
            max_bytes=100,
            size_of=lambda item: 1,
            checkpoint=_checkpoint,
        )


@pytest.mark.asyncio
async def test_history_window_drift_fails_explicitly(monkeypatch):
    entries = [_entry(2), _entry(1)]
    monkeypatch.setattr(history_module.postgres_log_service, "latest_snapshot_id", AsyncMock(return_value=2))
    monkeypatch.setattr(history_module.postgres_log_service, "list_latest_page", AsyncMock(return_value=entries))
    monkeypatch.setattr(
        history_module.postgres_log_service,
        "list_history_window_page",
        AsyncMock(side_effect=[[_entry(1)], []]),
    )
    reader = IngestLogHistoryReader("ac")
    window = await reader.plan_latest(
        "run-1", max_lines=2, max_bytes=100, size_of=lambda item: 1, checkpoint=_checkpoint
    )

    with pytest.raises(RuntimeError, match="expected=2 actual=1"):
        [item async for item in reader.iter_window(window, checkpoint=_checkpoint)]


@pytest.mark.asyncio
async def test_legacy_window_drift_fails_explicitly(monkeypatch):
    messages = [_redis_message(2), _redis_message(1)]
    redis = SimpleNamespace(
        xrevrange=AsyncMock(side_effect=[[messages[0]], messages]),
        xrange=AsyncMock(side_effect=[[_redis_message(1)], []]),
    )
    monkeypatch.setattr(history_module.postgres_log_service, "latest_snapshot_id", AsyncMock(return_value=0))
    monkeypatch.setattr(history_module, "get_redis_client", AsyncMock(return_value=redis))
    monkeypatch.setattr(history_module, "decode_batch", _decode_legacy)
    reader = IngestLogHistoryReader("ac")
    window = await reader.plan_latest(
        "run-1", max_lines=2, max_bytes=100, size_of=lambda item: 1, checkpoint=_checkpoint
    )

    with pytest.raises(RuntimeError, match="expected=2 actual=1"):
        [item async for item in reader.iter_window(window, checkpoint=_checkpoint)]
