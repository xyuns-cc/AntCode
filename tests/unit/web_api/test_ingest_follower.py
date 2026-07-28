"""IngestLogFollower：历史读取归一化、跟随计数、架构约束。"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import antcode_web_api.streams.ingest_follower as follower_module
import pytest
from antcode_core.infrastructure.redis.sse_event_stream import sse_event_stream_key
from antcode_web_api.streams.ingest_cursor import latest_stream_id
from antcode_web_api.streams.ingest_follower import IngestLogFollower

from tests.unit.web_api.fake_stream_capacity import make_broker

EVENT_KEY = sse_event_stream_key("ac")


def _event_fields(content: str, sequence: int) -> dict[str, str]:
    message = {
        "type": "log_line",
        "run_id": "run-1",
        "data": {"content": content, "sequence": sequence},
    }
    return {"payload": json.dumps(message)}


@pytest.mark.asyncio
async def test_follow_startup_failure_rolls_back_reference_and_task(monkeypatch):
    follower = IngestLogFollower(namespace="ac")
    monkeypatch.setattr(follower_module, "get_redis_client", AsyncMock(return_value=None))

    with pytest.raises(RuntimeError, match="ingest follower 初始化失败"):
        await follower.follow("run-1")

    assert follower._follow_counts == {}
    assert follower._ingest_task is None
    assert follower._ingest_running is False
    assert follower.healthy() is False


@pytest.mark.asyncio
async def test_web_api_lifespan_starts_follower_before_log_service(monkeypatch):
    import antcode_web_api.lifespan as lifespan_module
    from antcode_core.application.services.workers.distributed_log_service import distributed_log_service

    order: list[str] = []
    monkeypatch.setattr(
        follower_module.ingest_log_follower,
        "start",
        AsyncMock(side_effect=lambda: order.append("follower")),
    )
    monkeypatch.setattr(distributed_log_service, "start", AsyncMock(side_effect=lambda: order.append("service")))
    monkeypatch.setattr(distributed_log_service, "set_notifier", MagicMock())

    await lifespan_module._init_distributed_log()

    assert order == ["follower", "service"]


@pytest.mark.asyncio
async def test_successful_retry_clears_startup_error_and_restores_health(monkeypatch):
    follower = IngestLogFollower(namespace="ac")
    blocked = asyncio.Event()

    async def xread(*_args, **_kwargs):
        await blocked.wait()

    redis = SimpleNamespace(
        xrevrange=AsyncMock(return_value=[]),
        xread=AsyncMock(side_effect=xread),
    )
    monkeypatch.setattr(
        follower_module,
        "get_redis_client",
        AsyncMock(side_effect=[None, redis]),
    )

    with pytest.raises(RuntimeError, match="ingest follower 初始化失败"):
        await follower.follow("run-1")
    assert follower.healthy() is False

    await follower.follow("run-1")
    assert follower.healthy() is True
    await follower.unfollow("run-1")
    assert follower.healthy() is True
    await follower.shutdown()


@pytest.mark.asyncio
async def test_follow_unfollow_refcounts_shared_ingest_task(monkeypatch):
    follower = IngestLogFollower(namespace="ac")
    ensure = AsyncMock()
    stop = AsyncMock()
    monkeypatch.setattr(follower, "_ensure_ingest_task", ensure)
    monkeypatch.setattr(follower, "_stop_ingest_task", stop)

    await follower.follow("run-1")
    await follower.follow("run-1")
    assert follower._follow_counts["run-1"] == 2

    await follower.unfollow("run-1")
    stop.assert_not_awaited()

    await follower.unfollow("run-1")
    assert "run-1" not in follower._follow_counts
    stop.assert_not_awaited()


def test_ingest_follower_has_no_object_storage_history_reader():
    source = follower_module.__loader__.get_source(follower_module.__name__)

    assert "_send_history_from_s3" not in source
    assert "get_log_storage" not in source
    assert "presigned" not in source.lower()
    assert "aiohttp" not in source


@pytest.mark.asyncio
async def test_latest_stream_id_resolves_dollar_once_to_fixed_id():
    redis = SimpleNamespace(xrevrange=AsyncMock(return_value=[(b"42-7", {})]))

    cursor = await latest_stream_id(redis, EVENT_KEY)

    assert cursor == "42-7"
    redis.xrevrange.assert_awaited_once_with(EVENT_KEY, count=1)


@pytest.mark.asyncio
async def test_lifecycle_follower_starts_at_event_tail_and_delivers_new_frame(monkeypatch):
    follower = IngestLogFollower(namespace="ac", block_ms=10)
    broker = make_broker()
    monkeypatch.setattr(follower_module, "run_stream_broker", broker)
    read_count = 0
    release_new_frame = asyncio.Event()

    async def xread(cursors, **_kwargs):
        nonlocal read_count
        read_count += 1
        if read_count == 1:
            assert cursors == {EVENT_KEY: "5-0"}
            return [(EVENT_KEY.encode(), [(b"6-0", _event_fields("idle-frame", 6))])]
        if read_count == 2:
            await release_new_frame.wait()
            return [(EVENT_KEY.encode(), [(b"7-0", _event_fields("new-frame", 7))])]
        await asyncio.Event().wait()

    redis = SimpleNamespace(
        xrevrange=AsyncMock(return_value=[(b"5-0", {})]),
        xread=AsyncMock(side_effect=xread),
    )
    monkeypatch.setattr(follower_module, "get_redis_client", AsyncMock(return_value=redis))

    await follower.start()
    for _ in range(20):
        if follower._resume_cursors.get(EVENT_KEY) == "6-0":
            break
        await asyncio.sleep(0)
    assert follower._resume_cursors == {EVENT_KEY: "6-0"}
    redis.xrevrange.assert_awaited_once_with(EVENT_KEY, count=1)

    subscription = await broker.subscribe("run-1", user_id=7)
    await follower.follow("run-1")
    release_new_frame.set()
    message = await broker.get_message(subscription, timeout=0.1)

    assert message["data"]["content"] == "new-frame"
    assert message["data"]["sequence"] == 7
    assert follower._resume_cursors[EVENT_KEY] == "7-0"
    await follower.unfollow("run-1")
    await broker.unsubscribe(subscription)
    await follower.shutdown()


@pytest.mark.asyncio
async def test_trimmed_event_stream_resumes_at_first_available_message(monkeypatch):
    follower = IngestLogFollower(namespace="ac", block_ms=10)
    follower._resume_cursors[EVENT_KEY] = "1-0"
    broker = make_broker()
    subscription = await broker.subscribe("run-1", user_id=7)
    monkeypatch.setattr(follower_module, "run_stream_broker", broker)
    blocked = asyncio.Event()

    async def xread(cursors, **_kwargs):
        if cursors[EVENT_KEY] == "1-0":
            return [(EVENT_KEY.encode(), [(b"50-0", _event_fields("after-trim", 50))])]
        await blocked.wait()

    redis = SimpleNamespace(
        xrevrange=AsyncMock(),
        xread=AsyncMock(side_effect=xread),
    )
    monkeypatch.setattr(follower_module, "get_redis_client", AsyncMock(return_value=redis))

    try:
        await follower.start()
        message = await broker.get_message(subscription, timeout=0.2)
        assert message["data"]["content"] == "after-trim"
        assert follower._resume_cursors[EVENT_KEY] == "50-0"
        redis.xrevrange.assert_not_awaited()
    finally:
        await follower.shutdown()
        await broker.unsubscribe(subscription)


@pytest.mark.asyncio
async def test_raw_ingest_stream_is_not_distributed_or_added_to_cursor(monkeypatch):
    follower = IngestLogFollower(namespace="ac")
    broker = SimpleNamespace(
        subscribed_runs=lambda: {"run-1"},
        publish=MagicMock(),
    )
    monkeypatch.setattr(follower_module, "run_stream_broker", broker)
    redis = SimpleNamespace(
        xread=AsyncMock(
            return_value=[
                (b"ac:log:ingest", [(b"60-0", {b"p": b"raw-ingest-protobuf"})]),
            ]
        ),
        xadd=AsyncMock(),
    )
    context = follower_module._EventReadContext(
        redis=redis,
        event_key=EVENT_KEY,
        cursors={EVENT_KEY: "50-0"},
    )

    await follower._read_once(context)

    redis.xread.assert_awaited_once_with(
        {EVENT_KEY: "50-0"},
        count=200,
        block=5000,
    )
    broker.publish.assert_not_called()
    assert context.cursors == {EVENT_KEY: "50-0"}
    assert follower._resume_cursors == {}
    assert redis.xadd.await_args.args[1]["source_stream"] == "ac:log:ingest"
