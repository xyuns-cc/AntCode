"""Gateway SpiderData retention 契约测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_contracts import data_pb2
from antcode_gateway.handlers.spider_data import SpiderDataHandler
from antcode_worker.transport.redis.transport import RedisTransport


def _redis_pair() -> tuple[MagicMock, MagicMock]:
    pipeline = MagicMock()
    pipeline.execute = AsyncMock(return_value=[])
    redis = MagicMock()
    redis.pipeline.return_value = pipeline
    redis.script_load = AsyncMock(return_value="spider-script-sha")
    redis.evalsha = AsyncMock(side_effect=lambda *args: [int(args[19]), int(args[19]), 0])
    redis.eval = AsyncMock(return_value=1)
    redis.zrem = AsyncMock()
    return redis, pipeline


def _batch(item_count: int) -> data_pb2.SpiderDataBatch:
    return data_pb2.SpiderDataBatch(
        worker_id="worker-1",
        run_id="run-1",
        project_id="project-1",
        lease_id="lease-1",
        items=[
            data_pb2.SpiderDataItem(
                item_id=f"item-{sequence}",
                spider_name="rule",
                data=b"{}",
                sequence=sequence,
            )
            for sequence in range(1, item_count + 1)
        ],
        meta=data_pb2.SpiderMetaUpdate(status="running", items_count=item_count),
    )


def test_gateway_and_direct_share_retention_semantics(monkeypatch) -> None:
    monkeypatch.setenv("SPIDER_STREAM_MAXLEN", "123")
    monkeypatch.setenv("SPIDER_META_TTL_SECONDS", "456")
    monkeypatch.setenv("ANTCODE_SPIDER_STREAM_MAXLEN", "123")
    monkeypatch.setenv("ANTCODE_SPIDER_META_TTL_SECONDS", "456")

    gateway = SpiderDataHandler(redis_client=MagicMock())
    direct = RedisTransport(redis_url="redis://localhost", worker_id="worker-1")

    assert gateway._retention == direct._spider_retention


@pytest.mark.asyncio
async def test_gateway_default_keeps_more_than_10000_items(monkeypatch) -> None:
    monkeypatch.delenv("SPIDER_STREAM_MAXLEN", raising=False)
    monkeypatch.delenv("SPIDER_META_TTL_SECONDS", raising=False)
    monkeypatch.setenv("SPIDER_MAX_BATCH_ITEMS", "10001")
    monkeypatch.setenv("SPIDER_MAX_BATCH_BYTES", "16777216")
    redis, pipeline = _redis_pair()
    handler = SpiderDataHandler(redis_client=redis)

    accepted, failed = await handler.handle_batch(_batch(10001))

    assert (accepted, failed) == (10001, 0)
    redis.evalsha.assert_awaited_once()
    script_args = redis.evalsha.await_args.args
    assert script_args[17:20] == (0, 0, 10001)
    assert redis.eval.await_args.args[16] == 0
    pipeline.xadd.assert_not_called()


@pytest.mark.asyncio
async def test_gateway_explicit_retention_covers_stream_meta_index(monkeypatch) -> None:
    monkeypatch.setenv("SPIDER_STREAM_MAXLEN", "123")
    monkeypatch.setenv("SPIDER_META_TTL_SECONDS", "456")
    redis, pipeline = _redis_pair()
    handler = SpiderDataHandler(redis_client=redis)

    assert await handler.handle_batch(_batch(1)) == (1, 0)

    script_args = redis.evalsha.await_args.args
    assert script_args[17:20] == (123, 456, 1)
    assert redis.eval.await_args.args[16] == 456
    pipeline.xadd.assert_not_called()


@pytest.mark.asyncio
async def test_gateway_meta_only_batch_exposes_pipeline_failure() -> None:
    redis, pipeline = _redis_pair()
    redis.eval.side_effect = RuntimeError("redis unavailable")
    handler = SpiderDataHandler(redis_client=redis)
    batch = data_pb2.SpiderDataBatch(
        worker_id="worker-1",
        run_id="run-1",
        project_id="project-1",
        lease_id="lease-1",
        meta=data_pb2.SpiderMetaUpdate(status="completed", items_count=0),
    )

    with pytest.raises(RuntimeError, match="redis unavailable"):
        await handler.handle_batch(batch)


@pytest.mark.parametrize("name", ["SPIDER_STREAM_MAXLEN", "SPIDER_META_TTL_SECONDS"])
@pytest.mark.parametrize("value", ["", "   ", "-1", "1.5", "invalid"])
def test_gateway_invalid_retention_is_rejected(monkeypatch, name: str, value: str) -> None:
    monkeypatch.delenv("SPIDER_STREAM_MAXLEN", raising=False)
    monkeypatch.delenv("SPIDER_META_TTL_SECONDS", raising=False)
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match="非负整数"):
        SpiderDataHandler(redis_client=MagicMock())
