"""SSE 初始状态与历史回放协议。"""

import antcode_web_api.streams.log_stream_service as svc_module
import pytest
from antcode_web_api.streams.sse import build_log_line_message

from tests.unit.web_api.log_stream_test_support import (
    drain_until_history_end,
    history_entry,
    parse_frame,
    parse_frame_id,
    setup_stream,
)


@pytest.mark.asyncio
async def test_frame_sequence_history_then_realtime(monkeypatch):
    history = [
        history_entry("stdout", "line-1", 1),
        history_entry("stdout", "line-2", 2),
    ]
    broker, follower, generator = setup_stream(monkeypatch, history)

    frames = await drain_until_history_end(generator)

    events = [event for event, _ in frames]
    assert events == ["run_status", "historical_logs_start", "log_line", "log_line", "historical_logs_end"]
    assert frames[-1][1]["sent_lines"] == 2
    assert frames[2][1]["data"]["sequence"] == 1

    # 实时帧：sequence 高于历史阈值 → 透传
    broker.publish(
        "run-1",
        build_log_line_message(
            "run-1", log_type="stdout", content="line-3", timestamp=None, sequence=3, source="realtime"
        ),
    )
    event, data = parse_frame(await anext(generator))
    assert event == "log_line"
    assert data["data"]["content"] == "line-3"

    await generator.aclose()
    follower.unfollow.assert_awaited_once_with("run-1")
    assert (await broker.stats())["total_subscriptions"] == 0


@pytest.mark.asyncio
async def test_no_history_emits_no_historical_logs(monkeypatch):
    _, _, generator = setup_stream(monkeypatch, history=[])

    frames = await drain_until_history_end(generator)

    assert frames[-1][0] == "no_historical_logs"
    assert frames[-1][1]["sent_lines"] == 0
    await generator.aclose()


@pytest.mark.asyncio
async def test_initial_pg_history_uses_checkpoint_without_log_line_ids(monkeypatch):
    history = [{**history_entry("stdout", "persisted", 7, event_id="70-0:0"), "storage_id": 7}]
    _, follower, generator = setup_stream(monkeypatch, history=history)
    follower.history_reader.snapshot_id = 7

    assert parse_frame(await anext(generator))[0] == "run_status"
    assert parse_frame(await anext(generator))[0] == "historical_logs_start"
    history_frame = await anext(generator)
    checkpoint = await anext(generator)

    assert parse_frame(history_frame)[1]["data"]["content"] == "persisted"
    assert parse_frame_id(history_frame) is None
    assert parse_frame(checkpoint)[0] == "stream_cursor"
    assert parse_frame_id(checkpoint) == "pg:7"
    assert parse_frame(await anext(generator))[0] == "historical_logs_end"
    await generator.aclose()


@pytest.mark.asyncio
async def test_history_failure_is_explicit_and_terminates_stream(monkeypatch):
    broker, follower, generator = setup_stream(monkeypatch, history=[])
    follower.history_reader.error = RuntimeError("redis unavailable")

    assert parse_frame(await anext(generator))[0] == "run_status"
    assert parse_frame(await anext(generator))[0] == "historical_logs_start"
    event, data = parse_frame(await anext(generator))

    assert event == "stream_error"
    assert data["code"] == "history_unavailable"
    with pytest.raises(StopAsyncIteration):
        await anext(generator)
    follower.unfollow.assert_awaited_once_with("run-1")
    assert (await broker.stats())["total_subscriptions"] == 0


@pytest.mark.asyncio
async def test_history_utf8_byte_budget_keeps_latest_and_marks_truncated(monkeypatch):
    older = history_entry("stdout", "older", 1)
    newest = history_entry("stdout", "你好", 2)
    newest["timestamp"] = "2026-07-17T00:00:00+00:00"
    message = build_log_line_message(
        "run-1",
        log_type="stdout",
        content=newest["content"],
        timestamp=newest["timestamp"],
        sequence=2,
        source="pg_history",
    )
    monkeypatch.setattr(svc_module, "HISTORY_MAX_BYTES", len(svc_module.format_sse_event("log_line", message)))
    _, _, generator = setup_stream(monkeypatch, [older, newest])

    frames = await drain_until_history_end(generator)

    assert len("你好".encode()) == 6
    assert [data["data"]["content"] for event, data in frames if event == "log_line"] == ["你好"]
    assert frames[-1][1]["truncated"] is True
    await generator.aclose()


@pytest.mark.asyncio
async def test_terminal_status_reports_progress_100(monkeypatch):
    _, _, generator = setup_stream(monkeypatch, history=[], status="success")

    event, data = parse_frame(await anext(generator))

    assert event == "run_status"
    assert data["data"]["progress"] == 100.0
    assert data["data"]["message"] == "任务执行成功"
    await generator.aclose()
