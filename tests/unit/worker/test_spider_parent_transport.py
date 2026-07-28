"""Worker 主进程 SpiderData transport 的 Direct/Gateway 契约测试。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_contracts import data_pb2
from antcode_worker.transport.gateway.transport import GatewayConfig, GatewayTransport
from antcode_worker.transport.redis.transport import RedisTransport


def _item(sequence: int = 1) -> dict:
    return {
        "item_id": f"item-{sequence}",
        "run_id": "run-1",
        "project_id": "project-1",
        "spider_name": "rule",
        "item_type": "default",
        "data": '{"title":"ok"}',
        "url": "https://example.com",
        "timestamp": "2026-07-13T00:00:00",
        "sequence": sequence,
    }


def _direct_transport() -> tuple[RedisTransport, MagicMock]:
    control = MagicMock()
    control.report_spider_items = AsyncMock(return_value=True)
    control.update_spider_meta = AsyncMock(return_value=True)
    transport = RedisTransport(
        redis_url="redis://localhost",
        worker_id="worker-1",
        direct_control=control,
    )
    transport._running = True
    transport._lease_id = "lease-1"
    transport._require_current_generation = AsyncMock()
    return transport, control


@pytest.mark.parametrize("name", ["ANTCODE_SPIDER_STREAM_MAXLEN", "ANTCODE_SPIDER_META_TTL_SECONDS"])
@pytest.mark.parametrize("value", ["", "   ", "-1", "1.5", "invalid"])
def test_direct_transport_invalid_retention_is_rejected(monkeypatch, name: str, value: str) -> None:
    monkeypatch.delenv("ANTCODE_SPIDER_STREAM_MAXLEN", raising=False)
    monkeypatch.delenv("ANTCODE_SPIDER_META_TTL_SECONDS", raising=False)
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match="非负整数"):
        RedisTransport(redis_url="redis://localhost", worker_id="worker-1")


@pytest.mark.asyncio
async def test_direct_transport_rejects_oversized_batch_before_http(monkeypatch) -> None:
    monkeypatch.delenv("ANTCODE_SPIDER_STREAM_MAXLEN", raising=False)
    monkeypatch.delenv("ANTCODE_SPIDER_META_TTL_SECONDS", raising=False)
    transport, control = _direct_transport()

    items_ok = await transport.report_spider_data("run-1", [_item(i) for i in range(1, 502)])
    meta_ok = await transport.update_spider_meta(
        "run-1",
        {"project_id": "project-1", "status": "completed", "items_count": "1"},
    )

    assert items_ok is False
    assert meta_ok is True
    control.report_spider_items.assert_not_awaited()
    assert control.update_spider_meta.await_args.kwargs["project_id"] == "project-1"


@pytest.mark.asyncio
async def test_direct_transport_rejects_invalid_item_json_before_http() -> None:
    transport, control = _direct_transport()
    item = _item()
    item["data"] = "{broken"

    assert await transport.report_spider_data("run-1", [item]) is False

    control.report_spider_items.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("data", ["NaN", "Infinity", "-Infinity"])
async def test_direct_transport_rejects_non_strict_json_before_http(data: str) -> None:
    transport, control = _direct_transport()
    item = _item()
    item["data"] = data

    assert await transport.report_spider_data("run-1", [item]) is False

    control.report_spider_items.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_transport_canonicalizes_item_id_before_http() -> None:
    transport, control = _direct_transport()
    item = _item()
    item["item_id"] = "  item-1  "

    assert await transport.report_spider_data("run-1", [item]) is True

    payload = control.report_spider_items.await_args.kwargs["items"][0]
    assert payload["item_id"] == "item-1"


@pytest.mark.asyncio
async def test_direct_transport_explicit_retention_covers_stream_meta_index(monkeypatch) -> None:
    monkeypatch.setenv("ANTCODE_SPIDER_STREAM_MAXLEN", "123")
    monkeypatch.setenv("ANTCODE_SPIDER_META_TTL_SECONDS", "456")
    transport, control = _direct_transport()

    assert await transport.report_spider_data("run-1", [_item()]) is True
    assert await transport.update_spider_meta(
        "run-1",
        {"project_id": "project-1", "status": "running", "items_count": "0"},
    )

    control.report_spider_items.assert_awaited_once()
    control.update_spider_meta.assert_awaited_once()


@pytest.mark.asyncio
async def test_direct_transport_ambiguous_index_failure_is_compensated() -> None:
    transport, control = _direct_transport()
    control.update_spider_meta.side_effect = RuntimeError("ambiguous write")

    assert (
        await transport.update_spider_meta(
            "run-1",
            {"project_id": "project-1", "status": "running"},
        )
        is False
    )

    control.update_spider_meta.assert_awaited_once()


@pytest.mark.asyncio
async def test_direct_transport_cancelled_meta_write_compensates_then_propagates() -> None:
    transport, control = _direct_transport()
    control.update_spider_meta.side_effect = asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await transport.update_spider_meta(
            "run-1",
            {"project_id": "project-1", "status": "running"},
        )

    control.update_spider_meta.assert_awaited_once()


@pytest.mark.asyncio
async def test_direct_transport_noop_nx_meta_failure_does_not_zrem() -> None:
    """W4: ZADD NX 命中已存在 entry(no-op, 结果 0) 时，后续 meta 写瞬时失败
    不得 ZREM 掉仍存活的 run（否则把更早成功写入的 run 从 index 永久摘除）。"""
    transport, control = _direct_transport()
    control.update_spider_meta.side_effect = RuntimeError("transient meta write")

    assert (
        await transport.update_spider_meta(
            "run-1",
            {"project_id": "project-1", "status": "running"},
        )
        is False
    )

    control.update_spider_meta.assert_awaited_once()


@pytest.mark.asyncio
async def test_gateway_transport_reuses_worker_identity_and_auth_metadata() -> None:
    transport = GatewayTransport(
        gateway_config=GatewayConfig(
            gateway_host="gateway.example.com",
            gateway_port=443,
            worker_id="worker-real-1",
            use_tls=True,
        )
    )
    captured: list[tuple[data_pb2.SpiderDataBatch, list[tuple[str, str]]]] = []

    async def stream(requests, metadata):
        batches = [batch async for batch in requests]
        captured.extend((batch, metadata) for batch in batches)
        return data_pb2.SpiderDataAck(accepted=len(batches[0].items), failed=0)

    stub = MagicMock()
    stub.StreamSpiderData = AsyncMock(side_effect=stream)
    authenticator = MagicMock()
    authenticator.get_metadata.return_value = [
        ("x-api-key", "parent-key"),
        ("x-worker-id", "worker-real-1"),
    ]
    transport._running = True
    transport._data_stub = stub
    transport._authenticator = authenticator

    assert await transport.report_spider_data("run-1", [_item()]) is True
    assert await transport.update_spider_meta(
        "run-1",
        {"project_id": "project-1", "status": "completed", "items_count": "1"},
    )

    item_batch, metadata = captured[0]
    meta_batch, _ = captured[1]
    assert item_batch.worker_id == "worker-real-1"
    assert item_batch.project_id == "project-1"
    assert item_batch.items[0].data == b'{"title":"ok"}'
    assert metadata == authenticator.get_metadata.return_value
    assert meta_batch.worker_id == "worker-real-1"
    assert meta_batch.meta.status == "completed"
