from types import SimpleNamespace
from unittest.mock import AsyncMock

import antcode_web_api.streams.ingest_follower as follower_module
import antcode_web_api.streams.log_notifier as notifier_module
import antcode_web_api.streams.sse_event_stream as event_module
import pytest
from antcode_web_api.streams.ingest_follower import IngestLogFollower
from antcode_web_api.streams.log_notifier import SSELogNotifier
from antcode_web_api.streams.log_stream_replay import ReplayState
from antcode_web_api.streams.sse import build_log_line_message
from antcode_web_api.streams.sse_event_stream import decode_sse_event, publish_sse_event

from tests.unit.web_api.fake_stream_capacity import make_broker


@pytest.mark.asyncio
async def test_publish_sse_event_writes_bounded_redis_stream(monkeypatch):
    redis = SimpleNamespace(eval=AsyncMock(return_value=["1-0", 10, 0, 1]))
    monkeypatch.setattr(event_module, "get_redis_client", AsyncMock(return_value=redis))
    message = {"type": "log_line", "run_id": "run-1", "data": {"content": "hello"}}

    await publish_sse_event(message)

    payload = redis.eval.await_args.args[6]
    assert decode_sse_event({"payload": payload}) == message
    assert redis.eval.await_args.args[-1] == event_module.SSE_EVENT_STREAM_MAXLEN


@pytest.mark.asyncio
async def test_http_notifier_always_publishes_cross_process_event(monkeypatch):
    publish = AsyncMock()
    monkeypatch.setattr(notifier_module, "publish_sse_event", publish)
    notifier = SSELogNotifier()

    assert await notifier.has_connections("run-1") is True
    await notifier.send_log(
        run_id="run-1",
        log_type="stdout",
        content="hello",
        level="INFO",
        sequence=7,
        storage_id=51,
    )

    message = publish.await_args.args[0]
    assert message["run_id"] == "run-1"
    assert message["data"]["sequence"] == 7
    assert message["data"]["storage_id"] == 51


def test_delayed_http_notification_is_filtered_by_gap_storage_watermark():
    state = ReplayState()
    state.advance_storage_watermark(51)
    delayed = build_log_line_message(
        "run-1",
        log_type="stdout",
        content="hello",
        timestamp=None,
        sequence=7,
        source="task_execution",
        storage_id=51,
    )

    assert state.overlaps(delayed) is True


@pytest.mark.asyncio
async def test_follower_delivers_persisted_log_event_to_local_subscriber(monkeypatch):
    broker = make_broker()
    subscription = await broker.subscribe("run-1", user_id=7)
    monkeypatch.setattr(follower_module, "run_stream_broker", broker)
    follower = IngestLogFollower(namespace="ac")
    fields = {
        "payload": (
            '{"type":"log_line","run_id":"run-1","data":'
            '{"content":"hello","sequence":42,"event_id":"9-0:0","source":"realtime"}}'
        )
    }

    follower._publish_message(fields)

    message = await broker.get_message(subscription, timeout=0.1)
    assert message["data"]["content"] == "hello"
    assert message["data"]["sequence"] == 42
    assert message["data"]["event_id"] == "9-0:0"
    await broker.unsubscribe(subscription)


@pytest.mark.asyncio
async def test_follower_delivers_run_status_event_to_local_subscriber(monkeypatch):
    broker = make_broker()
    subscription = await broker.subscribe("run-1", user_id=7)
    monkeypatch.setattr(follower_module, "run_stream_broker", broker)
    fields = {
        "payload": (
            '{"type":"run_status","run_id":"run-1","data":{"status":"success","progress":100.0,"message":"done"}}'
        )
    }

    IngestLogFollower(namespace="ac")._publish_message(fields)

    message = await broker.get_message(subscription, timeout=0.1)
    assert message["type"] == "run_status"
    assert message["data"]["status"] == "success"
    await broker.unsubscribe(subscription)
