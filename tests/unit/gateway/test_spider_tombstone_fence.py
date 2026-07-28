"""Gateway Spider tombstone fencing and index compensation."""

from __future__ import annotations

from typing import Any

import pytest
from antcode_contracts import data_pb2
from antcode_gateway.handlers.spider_data import SpiderDataHandler
from antcode_gateway.handlers.spider_item_writer import SpiderItemWriteResult


class _Redis:
    def __init__(
        self,
        events: list[str],
        index_error: Exception | None = None,
        *,
        zadd_result: int = 1,
    ) -> None:
        self._events = events
        self._index_error = index_error
        self._zadd_result = zadd_result

    async def zrem(self, key: str, run_id: str) -> None:
        assert (key, run_id) == ("antcode:spider:index:project-1", "run-1")
        self._events.append("zrem")


class _ItemWriter:
    def __init__(self, events: list[str], error: Exception | None = None) -> None:
        self._events = events
        self._error = error

    async def write(
        self,
        _stream_key: str,
        _marker_key: str,
        _marker_order_key: str,
        *,
        identity,
        tombstone_key: str,
        index_key: str,
        index_expiry_key: str,
        payloads: list[dict[str, Any]],
    ) -> SpiderItemWriteResult:
        assert tombstone_key == "{antcode}:spider:run-1:tombstone"
        assert index_key == "{antcode}:spider:index:project-1"
        assert index_expiry_key == "{antcode}:spider:index:expiry:project-1"
        assert identity.owner_token == "worker-1:lease-1"
        self._events.append("items")
        if self._error:
            raise self._error
        return SpiderItemWriteResult(len(payloads), len(payloads), 0)


class _MetaWriter:
    def __init__(self, events: list[str], error: Exception | None = None) -> None:
        self._events = events
        self._error = error

    async def write(
        self,
        _meta_key: str,
        tombstone_key: str,
        *,
        identity,
        marker_key: str,
        index_key: str,
        index_expiry_key: str,
        fields: dict[str, Any],
    ) -> None:
        assert tombstone_key == "{antcode}:spider:run-1:tombstone"
        assert marker_key == "{antcode}:spider:run-1:item-ids"
        assert index_key == "{antcode}:spider:index:project-1"
        assert index_expiry_key == "{antcode}:spider:index:expiry:project-1"
        assert identity.owner_token == "worker-1:lease-1"
        assert fields["project_id"] == "project-1"
        self._events.append("meta")
        if self._error:
            raise self._error


def _batch(*, with_item: bool = True) -> data_pb2.SpiderDataBatch:
    batch = data_pb2.SpiderDataBatch(
        worker_id="worker-1",
        run_id="run-1",
        project_id="project-1",
        lease_id="lease-1",
        meta=data_pb2.SpiderMetaUpdate(status="running"),
    )
    if with_item:
        batch.items.append(data_pb2.SpiderDataItem(item_id="item-1", data=b"{}", sequence=1))
    return batch


@pytest.mark.asyncio
async def test_index_precedes_all_run_scope_writes() -> None:
    events: list[str] = []
    handler = SpiderDataHandler(
        redis_client=_Redis(events),
        item_writer=_ItemWriter(events),
        meta_writer=_MetaWriter(events),
    )

    assert await handler.handle_batch(_batch()) == (1, 0)

    assert events == ["items", "meta"]


@pytest.mark.asyncio
async def test_ambiguous_index_failure_is_compensated_before_run_writes() -> None:
    events: list[str] = []
    handler = SpiderDataHandler(
        redis_client=_Redis(events),
        item_writer=_ItemWriter(events, RuntimeError("atomic reply lost")),
        meta_writer=_MetaWriter(events),
    )

    with pytest.raises(RuntimeError, match="reply lost"):
        await handler.handle_batch(_batch())

    assert events == ["items"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["items", "meta"])
async def test_run_scope_failure_compensates_project_index(failure: str) -> None:
    events: list[str] = []
    item_error = RuntimeError("item fenced") if failure == "items" else None
    meta_error = RuntimeError("meta fenced") if failure == "meta" else None
    handler = SpiderDataHandler(
        redis_client=_Redis(events),
        item_writer=_ItemWriter(events, item_error),
        meta_writer=_MetaWriter(events, meta_error),
    )

    with pytest.raises(RuntimeError, match="fenced"):
        await handler.handle_batch(_batch(with_item=failure == "items"))

    assert events == [failure]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["items", "meta"])
async def test_preexisting_index_entry_survives_failed_batch_compensation(failure: str) -> None:
    """D3 回归：ZADD NX 未新增（entry 由更早成功 batch 写入）时，
    后续 batch 的瞬时失败不得 ZREM 摘除存活 run。"""
    events: list[str] = []
    item_error = RuntimeError("item fenced") if failure == "items" else None
    meta_error = RuntimeError("meta fenced") if failure == "meta" else None
    handler = SpiderDataHandler(
        redis_client=_Redis(events, zadd_result=0),
        item_writer=_ItemWriter(events, item_error),
        meta_writer=_MetaWriter(events, meta_error),
    )

    with pytest.raises(RuntimeError, match="fenced"):
        await handler.handle_batch(_batch(with_item=failure == "items"))

    assert events == [failure]
    assert "zrem" not in events
