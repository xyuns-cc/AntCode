"""LogHandler current Proto stream behavior."""

import pytest
from antcode_contracts import data_pb2
from antcode_gateway.handlers.logs import LogHandler

LOG_FENCE_KEY_COUNT = 3


class _FakePipeline:
    def __init__(self):
        self.commands = []

    def xadd(self, *args, **kwargs):
        self.commands.append(("xadd", args, kwargs))
        return self

    async def execute(self):
        self.commands.append(("execute", (), {}))
        return ["1-0"]


class _FakeRedis:
    def __init__(self):
        self.pipe = _FakePipeline()
        self.eval_calls = []

    def pipeline(self, transaction=False):
        self.pipe.commands.append(("pipeline", (), {"transaction": transaction}))
        return self.pipe

    async def eval(self, *args):
        self.eval_calls.append(args)
        return [1, b"1-0"]


class _FailingRedis(_FakeRedis):
    async def eval(self, *args):
        raise RuntimeError("redis write failed")


def _batch():
    # P1-GW-05: batch_id 必须是对 entries 重算一致的内容哈希。
    from antcode_core.common.log_batch_hash import deterministic_batch_id

    batch = data_pb2.LogBatch(
        worker_id="worker-1",
        lease_id="lease-1",
        entries=[
            data_pb2.LogEntry(
                run_id="run-1",
                log_type=data_pb2.LOG_TYPE_STDOUT,
                content="line",
                sequence=1,
            )
        ],
    )
    batch.batch_id = deterministic_batch_id(batch.worker_id, batch.entries)
    return batch


@pytest.mark.asyncio
async def test_handle_log_batch_writes_single_proto_frame():
    redis = _FakeRedis()
    handler = LogHandler(redis_client=redis)

    assert await handler.handle_log_batch(_batch()) is True

    assert len(redis.eval_calls) == 1
    call = redis.eval_calls[0]
    assert call[1] == LOG_FENCE_KEY_COUNT
    assert call[3] == "{antcode}:log:ingest"
    decoded = data_pb2.LogBatch.FromString(call[-1])
    assert decoded.batch_id == _batch().batch_id


@pytest.mark.asyncio
async def test_handle_log_batch_fails_closed_on_redis_write_error():
    handler = LogHandler(redis_client=_FailingRedis())

    with pytest.raises(RuntimeError, match="redis write failed"):
        await handler.handle_log_batch(_batch())


@pytest.mark.asyncio
async def test_handle_log_batch_fails_closed_without_redis(monkeypatch):
    handler = LogHandler(redis_client=None)
    monkeypatch.setattr(handler, "_get_redis_client", lambda: _async_none())

    assert await handler.handle_log_batch(_batch()) is False


@pytest.mark.asyncio
async def test_handle_log_batch_accepts_exact_protobuf_byte_limit():
    batch = _batch()
    handler = LogHandler(
        redis_client=_FakeRedis(),
        max_batch_bytes=batch.ByteSize(),
        max_entry_content_bytes=len(batch.entries[0].content.encode("utf-8")),
    )

    assert await handler.handle_log_batch(batch) is True


@pytest.mark.asyncio
async def test_handle_log_batch_rejects_protobuf_byte_overflow_before_redis():
    batch = _batch()
    redis = _FakeRedis()
    handler = LogHandler(
        redis_client=redis,
        max_batch_bytes=batch.ByteSize() - 1,
    )

    with pytest.raises(ValueError, match="LogBatch protobuf bytes 超限"):
        await handler.handle_log_batch(batch)

    assert redis.eval_calls == []


@pytest.mark.asyncio
async def test_handle_log_batch_uses_utf8_content_bytes_before_redis():
    batch = _batch()
    batch.entries[0].content = "你"
    redis = _FakeRedis()
    handler = LogHandler(
        redis_client=redis,
        max_entry_content_bytes=2,
    )

    with pytest.raises(ValueError, match="LogEntry content bytes 超限"):
        await handler.handle_log_batch(batch)

    assert redis.eval_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("batch_id", ["", " ", " abc", "abc ", "x" * 129, "b" * 64])
async def test_handle_log_batch_rejects_missing_or_noncanonical_batch_id(batch_id):
    # P1-GW-03: batch_id 是重发去重幂等键，入口必须强制存在且规范。
    # P1-GW-05: 规范格式（64 位 hex）但与内容哈希不符的同样拒绝。
    batch = _batch()
    batch.batch_id = batch_id
    redis = _FakeRedis()
    handler = LogHandler(redis_client=redis)

    with pytest.raises(ValueError, match="batch_id"):
        await handler.handle_log_batch(batch)

    assert redis.eval_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("run_id", ["", " ", " run-1", "run-1 "])
async def test_handle_log_batch_rejects_noncanonical_run_id_before_redis(run_id):
    batch = _batch()
    batch.entries[0].run_id = run_id
    redis = _FakeRedis()
    handler = LogHandler(redis_client=redis)

    with pytest.raises(ValueError, match="run_id 非法"):
        await handler.handle_log_batch(batch)

    assert redis.eval_calls == []


def test_log_handler_rejects_non_positive_explicit_limits():
    with pytest.raises(ValueError, match="max_batch_bytes"):
        LogHandler(redis_client=_FakeRedis(), max_batch_bytes=0)


@pytest.mark.asyncio
async def test_empty_batch_still_enforces_actual_protobuf_bytes():
    batch = data_pb2.LogBatch(worker_id="worker-with-large-metadata")
    handler = LogHandler(redis_client=_FakeRedis(), max_batch_bytes=batch.ByteSize() - 1)

    with pytest.raises(ValueError, match="LogBatch protobuf bytes 超限"):
        await handler.handle_log_batch(batch)


async def _async_none():
    return None
