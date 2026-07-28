"""P1-FN-02: 派发 XADD 响应丢失的查重防线（避免双执行）。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.workers.worker_dispatcher import WorkerTaskDispatcher
from antcode_core.infrastructure.redis import stream_dedup
from antcode_core.infrastructure.redis import streams as streams_module
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError
from redis.exceptions import TimeoutError as RedisTimeoutError


def test_indeterminate_error_classification():
    assert stream_dedup.is_indeterminate_stream_error(RedisTimeoutError("timed out")) is True
    assert stream_dedup.is_indeterminate_stream_error(RedisConnectionError("reset")) is True
    assert stream_dedup.is_indeterminate_stream_error(ConnectionResetError("peer reset")) is True
    assert stream_dedup.is_indeterminate_stream_error(RuntimeError("Connection closed by server.")) is True
    # 确定性失败：参数/协议错误不允许触发查重放行
    assert stream_dedup.is_indeterminate_stream_error(ResponseError("wrong number of arguments")) is False
    assert stream_dedup.is_indeterminate_stream_error(ValueError("bad payload")) is False


@pytest.mark.asyncio
async def test_find_recent_field_values_scans_bounded_tail():
    entries = [
        (b"3-1", {b"dispatch_id": b"run-a", b"task_id": b"run-a"}),
        (b"2-1", {"dispatch_id": "run-b"}),
        (b"1-1", {b"other": b"x"}),
    ]
    client = SimpleNamespace(xrevrange=AsyncMock(return_value=entries))
    stream = SimpleNamespace(_get_client=AsyncMock(return_value=client))

    found = await stream_dedup.find_recent_field_values(
        stream,
        "stream",
        field="dispatch_id",
        values={"run-a", "run-b", "run-c"},
    )

    assert found == {"run-a", "run-b"}
    kwargs = client.xrevrange.await_args.kwargs
    assert kwargs["count"] == stream_dedup.DISPATCH_DEDUP_SCAN_COUNT


@pytest.mark.asyncio
async def test_confirm_skips_dedup_for_deterministic_failure(monkeypatch):
    find = AsyncMock(side_effect=AssertionError("确定性失败不得触发查重"))
    monkeypatch.setattr(stream_dedup, "find_recent_field_values", find)

    committed = await stream_dedup.confirm_dispatch_committed(
        object(),
        "stream",
        [{"dispatch_id": "run-1"}],
        error=ResponseError("wrong arguments"),
    )

    assert committed is False
    find.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirm_dedup_failure_falls_back_to_not_committed(monkeypatch):
    """查重本身失败（Redis 持续不可达）→ 按未提交判失败（残余风险由消费端 claim 兜底）。"""
    monkeypatch.setattr(
        stream_dedup,
        "find_recent_field_values",
        AsyncMock(side_effect=RedisConnectionError("still down")),
    )

    committed = await stream_dedup.confirm_dispatch_committed(
        object(),
        "stream",
        [{"dispatch_id": "run-1"}],
        error=RedisTimeoutError("response lost"),
    )

    assert committed is False


class _FakeStream:
    def __init__(self, *, xadd_error=None):
        self.messages: list[dict] | None = None
        self._xadd_error = xadd_error

    async def xgroup_create(self, *_args, **_kwargs):
        return True

    async def xadd_batch(self, _stream_key, messages):
        self.messages = messages
        if self._xadd_error is not None:
            raise self._xadd_error
        return ["1-1"]

    async def xpending(self, *_args, **_kwargs):
        return {"pending_count": 0}

    async def _get_client(self):
        raise RuntimeError("no redis in test")


def _worker():
    return SimpleNamespace(id=7, public_id="worker-7", name="Worker 7")


def _task(run_id: str = "run-1") -> dict:
    return {"task_id": run_id, "run_id": run_id, "project_id": "p-1"}


def _patch_stream(monkeypatch, fake: _FakeStream):
    monkeypatch.setattr(streams_module, "StreamClient", lambda *args, **kwargs: fake)


@pytest.mark.asyncio
async def test_send_batch_carries_deterministic_dispatch_id(monkeypatch):
    fake = _FakeStream()
    _patch_stream(monkeypatch, fake)
    dispatcher = WorkerTaskDispatcher()

    result = await dispatcher._send_batch_to_queue(worker=_worker(), tasks=[_task("run-42")], batch_id="b1")

    assert result["success"] is True
    assert fake.messages[0]["dispatch_id"] == "run-42"


@pytest.mark.asyncio
async def test_lost_response_with_committed_write_is_treated_as_success(monkeypatch):
    """服务端已提交、响应丢失：查重命中 → 按成功处理，不触发 retry run 双执行。"""
    fake = _FakeStream(xadd_error=RedisTimeoutError("response lost"))
    _patch_stream(monkeypatch, fake)
    monkeypatch.setattr(stream_dedup, "find_recent_field_values", AsyncMock(return_value={"run-42"}))
    dispatcher = WorkerTaskDispatcher()

    result = await dispatcher._send_batch_to_queue(worker=_worker(), tasks=[_task("run-42")], batch_id="b1")

    assert result["success"] is True


@pytest.mark.asyncio
async def test_lost_response_with_confirmed_absence_fails(monkeypatch):
    """查重确认未写入 → 才允许判失败（进入补派/重试链路）。"""
    fake = _FakeStream(xadd_error=RedisTimeoutError("response lost"))
    _patch_stream(monkeypatch, fake)
    monkeypatch.setattr(stream_dedup, "find_recent_field_values", AsyncMock(return_value=set()))
    dispatcher = WorkerTaskDispatcher()

    result = await dispatcher._send_batch_to_queue(worker=_worker(), tasks=[_task("run-42")], batch_id="b1")

    assert result["success"] is False
