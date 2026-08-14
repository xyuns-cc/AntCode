"""Crawl batch exports must be lossless and uncapped."""

from __future__ import annotations

import csv
import io
import json
from collections.abc import AsyncIterator
from inspect import signature

import pytest
from antcode_web_api.services.crawl_batch_export import stream_batch_csv, stream_batch_json
from antcode_web_api.services.crawl_item_stream import CrawlBatchItem


def _item(sequence: int, payload: object) -> CrawlBatchItem:
    return CrawlBatchItem(
        sequence=sequence,
        payload=payload,
        url=f"https://example.test/{sequence}",
        timestamp="2026-07-30T00:00:00Z",
        run_id="run-1",
    )


def _source(items: list[CrawlBatchItem], calls: list[int | None]):
    async def source(_batch_id: str, limit: int | None) -> AsyncIterator[CrawlBatchItem]:
        calls.append(limit)
        for item in items:
            yield item

    return source


@pytest.mark.asyncio
async def test_csv_uses_stable_data_column_for_heterogeneous_payloads():
    payloads = [{"first": 1}, {"later": 2, "nested": {"ok": True}}]
    calls: list[int | None] = []

    body = "".join(
        [
            chunk
            async for chunk in stream_batch_csv(
                "batch-1", _source([_item(1, payloads[0]), _item(2, payloads[1])], calls)
            )
        ]
    )
    rows = list(csv.DictReader(io.StringIO(body)))

    assert calls == [None]
    assert list(rows[0]) == ["sequence", "url", "timestamp", "run_id", "data"]
    assert [json.loads(row["data"]) for row in rows] == payloads


@pytest.mark.asyncio
async def test_csv_always_emits_header_for_empty_batch():
    body = "".join([chunk async for chunk in stream_batch_csv("batch-1", _source([], []))])

    assert body == "sequence,url,timestamp,run_id,data\r\n"


@pytest.mark.asyncio
async def test_json_declares_complete_count_and_uses_uncapped_source():
    calls: list[int | None] = []
    items = [_item(1, {"first": 1}), _item(2, {"later": 2})]

    body = "".join([chunk async for chunk in stream_batch_json("batch-1", _source(items, calls))])
    payload = json.loads(body)

    assert calls == [None]
    assert payload["count"] == len(items)
    assert payload["truncated"] is False
    assert [item["data"] for item in payload["items"]] == [{"first": 1}, {"later": 2}]


@pytest.mark.asyncio
async def test_json_exports_more_than_legacy_ten_thousand_default():
    calls: list[int | None] = []
    total = 10_001

    async def source(_batch_id: str, limit: int | None) -> AsyncIterator[CrawlBatchItem]:
        calls.append(limit)
        for sequence in range(1, total + 1):
            yield _item(sequence, {"value": sequence})

    body = "".join([chunk async for chunk in stream_batch_json("batch-1", source)])
    payload = json.loads(body)

    assert calls == [None]
    assert payload["count"] == total
    assert len(payload["items"]) == total
    assert payload["items"][-1]["data"] == {"value": total}


def test_export_endpoint_does_not_expose_a_truncating_limit_parameter():
    from antcode_web_api.routes.v1.crawl import export_batch

    assert "limit" not in signature(export_batch).parameters
