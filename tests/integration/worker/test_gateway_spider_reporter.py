"""Reporter-to-Gateway Spider data integration contract."""

from __future__ import annotations

from typing import Any

import pytest
from antcode_contracts import data_pb2
from antcode_gateway.handlers.spider_data import SpiderDataHandler
from antcode_gateway.handlers.spider_item_writer import SpiderItemWriteResult
from antcode_worker.plugins.spider.data.gateway_reporter import GatewayDataReporter
from antcode_worker.plugins.spider.data.models import SpiderDataItem
from antcode_worker.transport.gateway.transport import GatewayConfig, GatewayTransport

TEST_FLUSH_INTERVAL_SECONDS = 3600.0


class MemoryRedis:
    def __init__(self) -> None:
        self.streams: dict[str, list[dict[str, Any]]] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.indexes: dict[str, dict[str, float]] = {}

    def pipeline(self, *, transaction: bool) -> MemoryPipeline:
        assert transaction is False
        return MemoryPipeline(self)

    async def expire(self, key: str, seconds: int) -> bool:
        del key, seconds
        return True


class MemoryPipeline:
    def __init__(self, redis: MemoryRedis) -> None:
        self._redis = redis
        self._operations: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def xadd(self, key: str, fields: dict[str, Any], **kwargs: Any) -> None:
        self._operations.append(("xadd", (key, fields), kwargs))

    def hset(self, key: str, *, mapping: dict[str, str]) -> None:
        self._operations.append(("hset", (key, mapping), {}))

    def zadd(self, key: str, mapping: dict[str, float], *, nx: bool) -> None:
        self._operations.append(("zadd", (key, mapping), {"nx": nx}))

    def expire(self, key: str, seconds: int) -> None:
        self._operations.append(("expire", (key, seconds), {}))

    def persist(self, key: str) -> None:
        self._operations.append(("persist", (key,), {}))

    async def execute(self) -> list[bool]:
        for name, args, kwargs in self._operations:
            self._apply(name, args, kwargs)
        return [True] * len(self._operations)

    def _apply(self, name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        if name == "xadd":
            key, fields = args
            self._redis.streams.setdefault(key, []).append(dict(fields))
            return
        if name == "hset":
            key, mapping = args
            self._redis.hashes.setdefault(key, {}).update(mapping)
            return
        if name == "zadd":
            key, mapping = args
            index = self._redis.indexes.setdefault(key, {})
            for member, score in mapping.items():
                if not kwargs["nx"] or member not in index:
                    index[member] = score


class MemoryItemWriter:
    def __init__(self, redis: MemoryRedis) -> None:
        self._redis = redis

    async def write(
        self,
        stream_key: str,
        marker_key: str,
        marker_order_key: str,
        *,
        identity,
        tombstone_key: str,
        index_key: str,
        index_expiry_key: str,
        payloads: list[dict[str, Any]],
    ) -> SpiderItemWriteResult:
        del marker_order_key, identity, tombstone_key, index_key, index_expiry_key
        markers = self._redis.hashes.setdefault(marker_key, {})
        stream = self._redis.streams.setdefault(stream_key, [])
        inserted = 0
        for payload in payloads:
            item_id = str(payload["item_id"])
            if item_id in markers:
                continue
            markers[item_id] = item_id
            stream.append(dict(payload))
            inserted += 1
        return SpiderItemWriteResult(len(payloads), inserted, len(payloads) - inserted)


class MemoryMetaWriter:
    def __init__(self, redis: MemoryRedis) -> None:
        self._redis = redis

    async def write(
        self,
        meta_key: str,
        tombstone_key: str,
        *,
        identity,
        marker_key: str,
        index_key: str,
        index_expiry_key: str,
        fields: dict[str, Any],
    ) -> None:
        del tombstone_key, identity, marker_key, index_key, index_expiry_key
        self._redis.hashes.setdefault(meta_key, {}).update(fields)


class InProcessSpiderDataStub:
    def __init__(self, handler: SpiderDataHandler) -> None:
        self._handler = handler

    async def StreamSpiderData(self, requests, metadata):
        del metadata
        accepted = 0
        failed = 0
        async for batch in requests:
            batch_accepted, batch_failed = await self._handler.handle_batch(batch)
            accepted += batch_accepted
            failed += batch_failed
        return data_pb2.SpiderDataAck(accepted=accepted, failed=failed)


def _transport(redis: MemoryRedis) -> GatewayTransport:
    handler = SpiderDataHandler(
        redis_client=redis,
        item_writer=MemoryItemWriter(redis),
        meta_writer=MemoryMetaWriter(redis),
    )
    transport = GatewayTransport(
        gateway_config=GatewayConfig(worker_id="worker-1"),
    )
    transport._running = True
    transport._data_stub = InProcessSpiderDataStub(handler)
    return transport


@pytest.mark.asyncio
async def test_reporter_persists_items_and_full_final_meta_through_gateway() -> None:
    redis = MemoryRedis()
    reporter = GatewayDataReporter(
        _transport(redis),
        run_id="run-1",
        project_id="project-1",
        spider_name="spider",
        batch_size=1,
        flush_interval=TEST_FLUSH_INTERVAL_SECONDS,
    )
    await reporter.start()
    item = SpiderDataItem(
        run_id="run-1",
        project_id="project-1",
        spider_name="spider",
        data={"title": "ok"},
    )

    assert await reporter.report_item(item) is True
    assert await reporter.finalize(
        "run-1",
        status="failed",
        items_count=1,
        pages_count=2,
        errors_count=1,
        duration_ms=12.5,
        errors=["parse"],
    )
    await reporter.stop()

    stream_rows = redis.streams["{antcode}:spider:run-1:data"]
    assert len(stream_rows) == 1
    assert stream_rows[0]["data"] == b'{"title": "ok"}'
    assert stream_rows[0]["sequence"] == "1"
    meta = redis.hashes["{antcode}:spider:run-1:meta"]
    assert meta["status"] == "failed"
    assert meta["spider_name"] == "spider"
    assert meta["items_count"] == "1"
    assert meta["pages_count"] == "2"
    assert meta["errors_count"] == "1"
    assert meta["duration_ms"] == "12.5"
    assert meta["errors"] == '["parse"]'
    assert meta["started_at"]
    assert meta["finished_at"]
