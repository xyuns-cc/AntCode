"""Crawl batch item stream reader tests."""

from collections.abc import Mapping, Sequence
from typing import Any

import pytest
from antcode_core.infrastructure.redis.keys import RedisKeys
from antcode_web_api.services.crawl_item_stream import CrawlBatchItemReader


def _entry(entry_id: str, sequence: int, item_id: str | None = None) -> tuple[bytes, dict[bytes, bytes]]:
    fields = {
        b"sequence": str(sequence).encode(),
        b"data": b'{"value": 1}',
        b"url": b"https://example.test",
        b"timestamp": b"2026-07-11T00:00:00Z",
    }
    if item_id is not None:
        fields[b"item_id"] = item_id.encode()
    return (entry_id.encode(), fields)


class FakeConnection:
    async def execute_query_dict(self, _query: str, values: list[Any]) -> list[dict[str, Any]]:
        assert values == ["batch-1"]
        return [{"run_id": "run-1"}, {"run_id": "run-2"}]


class FakeRedis:
    def __init__(self, pages: Mapping[str, Sequence[tuple[Any, Mapping[Any, Any]]]]) -> None:
        self._pages = pages
        self.calls: list[tuple[str, str, int]] = []

    async def xrange(self, name: str, min: str, max: str, count: int):
        assert max == "+"
        self.calls.append((name, min, count))
        return self._pages.get(f"{name}:{min}", [])[:count]


@pytest.mark.asyncio
async def test_limit_stops_before_reading_later_runs():
    redis = FakeRedis({"{ac}:spider:run-1:data:-": [_entry("1-0", 1), _entry("2-0", 2)]})
    reader = CrawlBatchItemReader(redis, FakeConnection(), RedisKeys("ac"))

    items = [item async for item in reader.iter_items("batch-1", limit=2)]

    assert [item.sequence for item in items] == [1, 2]
    assert redis.calls == [("{ac}:spider:run-1:data", "-", 2)]


@pytest.mark.asyncio
async def test_reader_pages_without_collecting_entire_stream(monkeypatch):
    import antcode_web_api.services.crawl_item_stream as stream_module

    monkeypatch.setattr(stream_module, "PAGE_SIZE", 2)
    redis = FakeRedis(
        {
            "{ac}:spider:run-1:data:-": [_entry("1-0", 1), _entry("2-0", 2)],
            "{ac}:spider:run-1:data:(2-0": [_entry("3-0", 3)],
        }
    )
    reader = CrawlBatchItemReader(redis, FakeConnection(), RedisKeys("ac"))

    items = [item async for item in reader.iter_items("batch-1", limit=3)]

    assert [item.sequence for item in items] == [1, 2, 3]
    assert redis.calls == [
        ("{ac}:spider:run-1:data", "-", 2),
        ("{ac}:spider:run-1:data", "(2-0", 1),
    ]


@pytest.mark.asyncio
async def test_duplicate_items_do_not_starve_later_entries():
    # P1-15 回归：重复 item_id 被跳过时不得消耗读取预算，否则同 run
    # 后面的未见条目会在总量未达 limit 时被静默丢弃。
    redis = FakeRedis(
        {
            "{ac}:spider:run-1:data:-": [
                _entry("1-0", 1, item_id="item-1"),
                _entry("2-0", 2, item_id="item-1"),
                _entry("3-0", 3, item_id="item-2"),
            ],
            "{ac}:spider:run-1:data:(2-0": [_entry("3-0", 3, item_id="item-2")],
        }
    )
    reader = CrawlBatchItemReader(redis, FakeConnection(), RedisKeys("ac"))

    items = [item async for item in reader.iter_items("batch-1", limit=2)]

    assert [item.sequence for item in items] == [1, 3]


@pytest.mark.asyncio
async def test_whole_run_resend_duplicates_do_not_consume_limit():
    # P1-15 回归：sender 恢复整批重发时，run-2 开头的重复条目全部被吸收，
    # 其后的新条目仍在 limit 内产出。
    redis = FakeRedis(
        {
            "{ac}:spider:run-1:data:-": [
                _entry("1-0", 1, item_id="a"),
                _entry("2-0", 2, item_id="b"),
            ],
            "{ac}:spider:run-2:data:-": [_entry("1-0", 1, item_id="a")],
            "{ac}:spider:run-2:data:(1-0": [_entry("2-0", 2, item_id="b")],
            "{ac}:spider:run-2:data:(2-0": [_entry("3-0", 3, item_id="c")],
        }
    )
    reader = CrawlBatchItemReader(redis, FakeConnection(), RedisKeys("ac"))

    items = [item async for item in reader.iter_items("batch-1", limit=3)]

    assert [(item.run_id, item.sequence) for item in items] == [("run-1", 1), ("run-1", 2), ("run-2", 3)]


@pytest.mark.asyncio
async def test_all_duplicate_run_stops_scanning_at_budget(monkeypatch):
    # C2 回归：sender 恢复整批重发产生的"全重复 run"不得为找一条不存在的
    # 新条目而翻遍整条 Stream；达到空扫预算即停扫（正确性不变，全重复 run
    # 本就无新条目可产出）。
    import antcode_web_api.services.crawl_item_stream as stream_module

    monkeypatch.setattr(stream_module, "PAGE_SIZE", 1)
    monkeypatch.setattr(stream_module, "MIN_DUP_SCAN_BUDGET", 2)
    monkeypatch.setattr(stream_module, "DUP_SCAN_BUDGET_MULTIPLIER", 1)
    redis = FakeRedis(
        {
            "{ac}:spider:run-1:data:-": [_entry("1-0", 1, item_id="a")],
            "{ac}:spider:run-2:data:-": [_entry("1-0", 1, item_id="a")],
            "{ac}:spider:run-2:data:(1-0": [_entry("2-0", 2, item_id="a")],
            "{ac}:spider:run-2:data:(2-0": [_entry("3-0", 3, item_id="a")],
            "{ac}:spider:run-2:data:(3-0": [_entry("4-0", 4, item_id="a")],
            "{ac}:spider:run-2:data:(4-0": [_entry("5-0", 5, item_id="a")],
        }
    )
    reader = CrawlBatchItemReader(redis, FakeConnection(), RedisKeys("ac"))

    items = [item async for item in reader.iter_items("batch-1", limit=4)]

    # 只有 run-1 的真·新条目被产出，run-2 全部重复不产出。
    assert [item.sequence for item in items] == [1]
    # run-2 预算 = max(remaining(=3) * 1, 2) = 3：空扫 3 条后停扫，
    # 深层游标 (3-0 / (4-0 从未被拉取（否则会翻遍整条 Stream）。
    run2_cursors = [cursor for (name, cursor, _count) in redis.calls if name == "{ac}:spider:run-2:data"]
    assert run2_cursors == ["-", "(1-0", "(2-0"]


@pytest.mark.asyncio
async def test_invalid_stream_entry_is_not_silently_skipped():
    redis = FakeRedis(
        {
            "{ac}:spider:run-1:data:-": [
                (b"1-0", {b"sequence": b"1", b"data": b"not-json"}),
            ]
        }
    )
    reader = CrawlBatchItemReader(redis, FakeConnection(), RedisKeys("ac"))

    with pytest.raises(ValueError, match="run_id=run-1 entry_id=1-0"):
        _ = [item async for item in reader.iter_items("batch-1", limit=1)]
