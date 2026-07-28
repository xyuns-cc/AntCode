import asyncio
from types import SimpleNamespace

import pytest
from antcode_contracts import data_pb2
from antcode_scrapy.pipelines.redis_pipeline import AntCodeRedisPipeline
from antcode_scrapy.sinks.gateway_sink import GatewaySpiderDataSink


class _GatewayStub:
    async def StreamSpiderData(self, batches, metadata):
        self.metadata = metadata
        self.batches = [batch async for batch in batches]
        return SimpleNamespace(accepted=1, failed=0)


class _BlockingGatewayStub:
    def __init__(self):
        self.started = asyncio.Event()

    async def StreamSpiderData(self, batches, metadata):
        del metadata
        _ = [batch async for batch in batches]
        self.started.set()
        await asyncio.Event().wait()


class _FailingGatewayStub:
    def __init__(self):
        self.release = asyncio.Event()
        self.started = asyncio.Event()

    async def StreamSpiderData(self, batches, metadata):
        del metadata
        _ = [batch async for batch in batches]
        self.started.set()
        await self.release.wait()
        raise RuntimeError("rpc failed")


class _Stats:
    def __init__(self):
        self.values = {"item_scraped_count": 3, "log_count/ERROR": 0}

    def get_value(self, key, default=0):
        return self.values.get(key, default)

    def inc_value(self, key, count=1):
        self.values[key] = self.values.get(key, 0) + count

    def set_value(self, key, value):
        self.values[key] = value


class _PipelineSink:
    async def close(self, final_meta):
        self.final_meta = final_meta
        return True, 0

    async def consume_written_count(self):
        return 3


@pytest.mark.asyncio
async def test_gateway_flush_tracks_unreported_written_count():
    sink = GatewaySpiderDataSink(endpoint="gateway:50051")
    sink._stub = _GatewayStub()
    sink._run_id = "run-1"
    sink._project_id = "project-1"
    sink._batch_items.append(data_pb2.SpiderDataItem(item_id="item-1"))

    assert await sink._flush() == (True, 1)
    assert await sink.consume_written_count() == 1
    assert await sink.consume_written_count() == 0


@pytest.mark.asyncio
async def test_gateway_flush_restores_items_when_send_is_cancelled():
    sink = GatewaySpiderDataSink(endpoint="gateway:50051")
    stub = _BlockingGatewayStub()
    sink._stub = stub
    sink._batch_items.append(data_pb2.SpiderDataItem(item_id="item-1"))
    task = asyncio.create_task(sink._flush())
    await stub.started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert [item.item_id for item in sink._batch_items] == ["item-1"]
    assert await sink.consume_written_count() == 0


@pytest.mark.asyncio
async def test_gateway_flush_restores_items_when_cancelled_during_recovery():
    sink = GatewaySpiderDataSink(endpoint="gateway:50051")
    stub = _FailingGatewayStub()
    sink._stub = stub
    sink._batch_items.append(data_pb2.SpiderDataItem(item_id="item-1"))
    task = asyncio.create_task(sink._flush())
    await stub.started.wait()
    await sink._lock.acquire()
    stub.release.set()
    await asyncio.sleep(0)
    task.cancel()
    sink._lock.release()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert [item.item_id for item in sink._batch_items] == ["item-1"]


@pytest.mark.asyncio
async def test_gateway_flush_rejects_ack_count_mismatch():
    sink = GatewaySpiderDataSink(endpoint="gateway:50051")
    sink._stub = _GatewayStub()
    sink._stub.StreamSpiderData = _zero_ack
    sink._batch_items.append(data_pb2.SpiderDataItem(item_id="item-1"))

    assert await sink._flush() == (False, 0)
    assert [item.item_id for item in sink._batch_items] == ["item-1"]


@pytest.mark.parametrize("value", ["", "0", "-1", "nan", "inf"])
def test_gateway_flush_interval_must_be_finite_positive(monkeypatch, value):
    monkeypatch.setenv("ANTCODE_SPIDER_GATEWAY_FLUSH_INTERVAL", value)

    with pytest.raises(ValueError, match="有限正数"):
        GatewaySpiderDataSink(endpoint="gateway:50051")


@pytest.mark.parametrize("value", ["", "0", "-1", "invalid"])
def test_gateway_batch_size_must_be_positive(monkeypatch, value):
    monkeypatch.setenv("ANTCODE_SPIDER_GATEWAY_BATCH_SIZE", value)

    with pytest.raises(ValueError, match="正整数"):
        GatewaySpiderDataSink(endpoint="gateway:50051")


async def _zero_ack(batches, metadata):
    del metadata
    _ = [batch async for batch in batches]
    return SimpleNamespace(accepted=0, failed=0)


@pytest.mark.asyncio
async def test_pipeline_close_counts_background_flush_writes():
    stats = _Stats()
    pipeline = AntCodeRedisPipeline()
    pipeline._enabled = True
    pipeline._sink = _PipelineSink()
    pipeline._run_id = "run-1"
    pipeline._started_at = None
    spider = SimpleNamespace(crawler=SimpleNamespace(stats=stats))

    await pipeline.close_spider(spider)

    assert stats.get_value("antcode/redis_items_written") == 3
    assert stats.get_value("antcode/final_flush_failed") == 0
