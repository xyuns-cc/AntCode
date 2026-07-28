"""SSE ingest follower malformed-frame isolation semantics."""

import asyncio
import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import antcode_web_api.streams.ingest_follower as follower_module
import pytest
from antcode_core.infrastructure.redis.sse_event_stream import sse_event_stream_key
from antcode_web_api.streams.ingest_dead_letter import (
    DLQ_MAX_FIELD_BYTES,
    DLQ_MAX_FIELDS,
    DLQ_MAXLEN,
    isolate_bad_ingest_frame,
)
from antcode_web_api.streams.ingest_follower import IngestLogFollower

from tests.unit.web_api.fake_stream_capacity import make_broker

EVENT_KEY = sse_event_stream_key("ac")


def _redis(*, xread, xadd) -> SimpleNamespace:
    return SimpleNamespace(
        xrevrange=AsyncMock(return_value=[]),
        xread=AsyncMock(side_effect=xread),
        xadd=AsyncMock(side_effect=xadd),
    )


@pytest.mark.asyncio
async def test_bad_frame_isolated_before_cursor_advances(monkeypatch):
    follower = IngestLogFollower(namespace="ac", block_ms=10)
    broker = make_broker()
    subscription = await broker.subscribe("run-1", user_id=7)
    monkeypatch.setattr(follower_module, "run_stream_broker", broker)
    valid = {
        "payload": json.dumps(
            {
                "type": "log_line",
                "run_id": "run-1",
                "data": {"content": "after-bad", "sequence": 2},
            }
        )
    }
    blocked = asyncio.Event()

    async def xread(cursors, **_kwargs):
        if cursors[EVENT_KEY] == "0-0":
            return [
                (
                    EVENT_KEY.encode(),
                    [
                        (b"1-0", {"payload": "{broken-json"}),
                        (b"2-0", valid),
                    ],
                )
            ]
        await blocked.wait()

    async def xadd(_key, _entry, **_kwargs):
        assert EVENT_KEY not in follower._resume_cursors

    redis = _redis(xread=xread, xadd=xadd)
    monkeypatch.setattr(follower_module, "get_redis_client", AsyncMock(return_value=redis))

    try:
        await follower.start()
        message = await broker.get_message(subscription, timeout=0.2)
        assert message["data"]["content"] == "after-bad"
        assert message["data"]["sequence"] == 2
        assert follower._resume_cursors[EVENT_KEY] == "2-0"
        assert follower.healthy() is True
        key, entry = redis.xadd.await_args.args
        assert key == "ac:dead_letter:sse_ingest"
        assert redis.xadd.await_args.kwargs == {"maxlen": DLQ_MAXLEN, "approximate": True}
        assert entry["source_stream"] == EVENT_KEY
        assert entry["source_message_id"] == "1-0"
        assert entry["decode_error"]
        assert json.loads(entry["raw_fields"])[0]["key"] == "payload"
    finally:
        await follower.shutdown()
        await broker.unsubscribe(subscription)


@pytest.mark.asyncio
async def test_dlq_failure_does_not_advance_cursor_and_keeps_unhealthy(monkeypatch):
    follower = IngestLogFollower(namespace="ac", block_ms=10)
    broker = make_broker()
    subscription = await broker.subscribe("run-1", user_id=7)
    monkeypatch.setattr(follower_module, "run_stream_broker", broker)
    second_read = asyncio.Event()
    read_cursors: list[dict[str, str]] = []

    async def xread(cursors, **_kwargs):
        read_cursors.append(dict(cursors))
        if len(read_cursors) == 1:
            return [(EVENT_KEY.encode(), [(b"1-0", {"payload": "{broken-json"})])]
        second_read.set()
        await asyncio.Event().wait()

    redis = _redis(xread=xread, xadd=RuntimeError("dlq unavailable"))
    monkeypatch.setattr(follower_module, "get_redis_client", AsyncMock(return_value=redis))

    try:
        await follower.start()
        await asyncio.wait_for(second_read.wait(), timeout=2.0)
        assert read_cursors[:2] == [
            {EVENT_KEY: "0-0"},
            {EVENT_KEY: "0-0"},
        ]
        assert EVENT_KEY not in follower._resume_cursors
        assert follower.healthy() is False
        assert follower._last_error == "dlq unavailable"
    finally:
        await follower.shutdown()
        await broker.unsubscribe(subscription)


@pytest.mark.asyncio
async def test_dead_letter_raw_fields_are_bounded():
    redis = SimpleNamespace(xadd=AsyncMock())
    fields = {f"field-{index}": b"x" * (DLQ_MAX_FIELD_BYTES + 1) for index in range(DLQ_MAX_FIELDS + 1)}

    await isolate_bad_ingest_frame(
        redis,
        namespace="ac",
        source_stream=EVENT_KEY,
        message_id="1-0",
        fields=fields,
        error=ValueError("bad frame"),
    )

    raw_fields = json.loads(redis.xadd.await_args.args[1]["raw_fields"])
    assert len(raw_fields) == DLQ_MAX_FIELDS
    assert all(item["truncated"] is True for item in raw_fields)
    assert all(len(base64.b64decode(item["value_base64"])) == DLQ_MAX_FIELD_BYTES for item in raw_fields)
