"""SSE 实时帧转发与历史重叠去重。"""

import json

import antcode_web_api.streams.ingest_follower as follower_module
import antcode_web_api.streams.log_stream_service as svc_module
import pytest
from antcode_web_api.streams.ingest_follower import IngestLogFollower
from antcode_web_api.streams.log_stream_replay import ReplayState
from antcode_web_api.streams.sse import build_log_line_message

from tests.unit.web_api.log_stream_test_support import (
    drain_until_history_end,
    history_entry,
    parse_frame,
    setup_stream,
)


@pytest.mark.asyncio
async def test_overlap_filter_only_drops_exact_positive_sequence(monkeypatch):
    history = [history_entry("stdout", "old", 5)]
    broker, _, generator = setup_stream(monkeypatch, history)
    await drain_until_history_end(generator)

    # seq=3 未在快照中，即使低于 5 也必须透传；只有快照实际发送的 seq=5 重叠。
    broker.publish(
        "run-1",
        build_log_line_message(
            "run-1", log_type="stdout", content="late-3", timestamp=None, sequence=3, source="realtime"
        ),
    )
    broker.publish(
        "run-1",
        build_log_line_message(
            "run-1", log_type="stdout", content="duplicate-5", timestamp=None, sequence=5, source="realtime"
        ),
    )
    broker.publish(
        "run-1",
        build_log_line_message(
            "run-1", log_type="stderr", content="err-0", timestamp=None, sequence=0, source="realtime"
        ),
    )

    first_event, first = parse_frame(await anext(generator))
    second_event, second = parse_frame(await anext(generator))
    assert first_event == second_event == "log_line"
    assert first["data"]["content"] == "late-3"
    assert second["data"]["content"] == "err-0"
    await generator.aclose()


def test_sequence_zero_is_a_stable_type_scoped_overlap_identity():
    state = ReplayState()
    state.record("stdout", sequence=0, event_id=None)

    assert state.overlaps_entry({"log_type": "stdout", "sequence": 0}) is True
    assert state.overlaps_entry({"log_type": "stderr", "sequence": 0}) is False

    state.confirm_persisted({"log_type": "stdout", "sequence": 0})
    assert state.overlaps_entry({"log_type": "stdout", "sequence": 0}) is False


@pytest.mark.asyncio
async def test_persisted_event_boundary_deduplicates_without_dropping_equal_content(monkeypatch):
    history = [history_entry("stdout", "same-line", 41, event_id="42-0:0")]
    broker, _, generator = setup_stream(monkeypatch, history)
    monkeypatch.setattr(follower_module, "run_stream_broker", broker)

    assert parse_frame(await anext(generator))[0] == "run_status"
    duplicate = build_log_line_message(
        "run-1",
        log_type="stdout",
        content="same-line",
        timestamp=None,
        sequence=41,
        source="realtime",
        event_id="42-0:0",
    )
    distinct = build_log_line_message(
        "run-1",
        log_type="stdout",
        content="same-line",
        timestamp=None,
        sequence=42,
        source="realtime",
        event_id="42-0:2",
    )
    follower = IngestLogFollower("ac")
    follower._publish_message({"payload": json.dumps(duplicate)})
    follower._publish_message({"payload": json.dumps(distinct)})

    assert parse_frame(await anext(generator))[0] == "historical_logs_start"
    assert parse_frame(await anext(generator))[1]["data"]["event_id"] == "42-0:0"
    assert parse_frame(await anext(generator))[0] == "historical_logs_end"
    event, realtime = parse_frame(await anext(generator))

    assert event == "log_line"
    assert realtime["data"]["content"] == "same-line"
    assert realtime["data"]["sequence"] == 42
    assert realtime["data"]["event_id"] == "42-0:2"
    await generator.aclose()


@pytest.mark.asyncio
async def test_frames_without_sequence_pass_through(monkeypatch):
    history = [history_entry("stdout", "old", 100)]
    broker, _, generator = setup_stream(monkeypatch, history)
    await drain_until_history_end(generator)

    broker.publish(
        "run-1",
        build_log_line_message(
            "run-1", log_type="stdout", content="legacy", timestamp=None, sequence=None, source="realtime"
        ),
    )

    event, data = parse_frame(await anext(generator))
    assert event == "log_line"
    assert data["data"]["content"] == "legacy"
    await generator.aclose()


@pytest.mark.asyncio
async def test_run_status_messages_are_forwarded(monkeypatch):
    broker, _, generator = setup_stream(monkeypatch, history=[])
    await drain_until_history_end(generator)

    broker.publish(
        "run-1",
        {"type": "run_status", "run_id": "run-1", "data": {"status": "success", "progress": 100.0, "message": "done"}},
    )

    event, data = parse_frame(await anext(generator))
    assert event == "run_status"
    assert data["data"]["status"] == "success"
    await generator.aclose()


@pytest.mark.asyncio
async def test_idle_stream_emits_ping(monkeypatch):
    monkeypatch.setattr(svc_module, "PING_INTERVAL_SECONDS", 0.01)
    _, _, generator = setup_stream(monkeypatch, history=[])
    await drain_until_history_end(generator)

    event, data = parse_frame(await anext(generator))

    assert event == "ping"
    assert data["type"] == "ping"
    await generator.aclose()


def test_replay_state_does_not_treat_larger_live_storage_id_as_range_watermark():
    state = ReplayState(storage_watermark=10)
    larger = build_log_line_message(
        "run-1",
        log_type="stdout",
        content="published-first",
        timestamp=None,
        sequence=12,
        source="persisted_realtime",
        storage_id=12,
    )
    smaller = build_log_line_message(
        "run-1",
        log_type="stdout",
        content="published-later",
        timestamp=None,
        sequence=11,
        source="persisted_realtime",
        storage_id=11,
    )

    state.record_message(larger)

    assert state.overlaps(larger) is True
    assert state.overlaps(smaller) is False
