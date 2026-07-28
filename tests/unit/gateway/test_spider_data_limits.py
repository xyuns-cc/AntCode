from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from antcode_contracts import data_pb2
from antcode_gateway.handlers.spider_data import SpiderDataHandler


def _item(item_id: str, *, data: bytes = b"{}") -> data_pb2.SpiderDataItem:
    return data_pb2.SpiderDataItem(
        item_id=item_id,
        spider_name="rule",
        item_type="default",
        data=data,
        sequence=1,
    )


def _batch(items: list[data_pb2.SpiderDataItem]) -> data_pb2.SpiderDataBatch:
    return data_pb2.SpiderDataBatch(
        worker_id="worker-1",
        run_id="run-1",
        project_id="project-1",
        items=items,
    )


def test_batch_item_limit_is_explicit(monkeypatch) -> None:
    monkeypatch.setenv("SPIDER_MAX_BATCH_ITEMS", "1")
    handler = SpiderDataHandler(redis_client=MagicMock())

    with pytest.raises(ValueError, match="batch items 超限"):
        handler._validate_batch_size(_batch([_item("one"), _item("two")]))


def test_batch_byte_limit_is_explicit(monkeypatch) -> None:
    monkeypatch.setenv("SPIDER_MAX_BATCH_BYTES", "3")
    handler = SpiderDataHandler(redis_client=MagicMock())

    with pytest.raises(ValueError, match="batch bytes 超限"):
        handler._validate_batch_size(_batch([_item("one")]))


def test_batch_byte_limit_includes_meta(monkeypatch) -> None:
    monkeypatch.setenv("SPIDER_MAX_BATCH_BYTES", "128")
    handler = SpiderDataHandler(redis_client=MagicMock())
    batch = _batch([])
    batch.meta.config = "x" * 256

    with pytest.raises(ValueError, match="batch bytes 超限"):
        handler._validate_batch_size(batch)


def test_oversized_item_is_counted_as_failed(monkeypatch) -> None:
    monkeypatch.setenv("SPIDER_MAX_ITEM_BYTES", "1")
    handler = SpiderDataHandler(redis_client=MagicMock())

    payloads, failed = handler._build_item_payloads(_batch([_item("one")]))

    assert payloads == []
    assert failed == 1


def test_item_byte_limit_includes_text_fields(monkeypatch) -> None:
    monkeypatch.setenv("SPIDER_MAX_ITEM_BYTES", "16")
    handler = SpiderDataHandler(redis_client=MagicMock())
    item = _item("one")
    item.url = "https://example.com/a-long-path"

    payloads, failed = handler._build_item_payloads(_batch([item]))

    assert payloads == []
    assert failed == 1


@pytest.mark.parametrize("data", [b"NaN", b"Infinity", b"-Infinity", b"{broken"])
def test_invalid_item_json_is_counted_as_failed(data: bytes) -> None:
    handler = SpiderDataHandler(redis_client=MagicMock())

    payloads, failed = handler._build_item_payloads(_batch([_item("one", data=data)]))

    assert payloads == []
    assert failed == 1


@pytest.mark.parametrize("value", ["", "   ", "0", "-1", "invalid"])
def test_invalid_gateway_spider_limit_is_rejected(monkeypatch, value: str) -> None:
    monkeypatch.setenv("SPIDER_MAX_BATCH_ITEMS", value)

    with pytest.raises(ValueError, match="正整数"):
        SpiderDataHandler(redis_client=MagicMock())
