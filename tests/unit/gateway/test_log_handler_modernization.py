"""LogHandler current Proto stream behavior."""

import pytest
from antcode_contracts import data_pb2
from antcode_gateway.handlers.logs import LogHandler


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

    def pipeline(self, transaction=False):
        self.pipe.commands.append(("pipeline", (), {"transaction": transaction}))
        return self.pipe


class _FailingPipeline(_FakePipeline):
    async def execute(self):
        raise RuntimeError("redis write failed")


class _FailingRedis(_FakeRedis):
    def __init__(self):
        self.pipe = _FailingPipeline()


def _batch():
    return data_pb2.LogBatch(
        worker_id="worker-1",
        entries=[
            data_pb2.LogEntry(
                run_id="run-1",
                log_type=data_pb2.LOG_TYPE_STDOUT,
                content="line",
                sequence=1,
            )
        ],
    )


@pytest.mark.asyncio
async def test_handle_log_batch_writes_single_proto_frame():
    redis = _FakeRedis()
    handler = LogHandler(redis_client=redis)

    assert await handler.handle_log_batch(_batch()) is True

    names = [command[0] for command in redis.pipe.commands]
    assert names == ["pipeline", "xadd", "execute"]
    fields = redis.pipe.commands[1][1][1]
    assert set(fields) == {b"p"}


@pytest.mark.asyncio
async def test_handle_log_batch_fails_closed_on_redis_write_error():
    handler = LogHandler(redis_client=_FailingRedis())

    assert await handler.handle_log_batch(_batch()) is False


@pytest.mark.asyncio
async def test_handle_log_batch_fails_closed_without_redis(monkeypatch):
    handler = LogHandler(redis_client=None)
    monkeypatch.setattr(handler, "_get_redis_client", lambda: _async_none())

    assert await handler.handle_log_batch(_batch()) is False


async def _async_none():
    return None
