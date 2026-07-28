"""SSE 持久化 gap 补偿与实时队列公平性。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import antcode_web_api.streams.log_stream_active as active_module
import antcode_web_api.streams.log_stream_service as svc_module
import pytest
from antcode_web_api.streams.log_stream_active import (
    ActiveLogStream,
    ActiveStreamConfig,
    ActiveStreamContext,
    ActiveStreamDependencies,
    LiveStreamState,
    _Deadlines,
    _monotonic,
)
from antcode_web_api.streams.log_stream_replay import ReplayState
from antcode_web_api.streams.sse import build_log_line_message

from tests.unit.web_api.log_stream_test_support import (
    GapReader,
    drain_until_history_end,
    history_entry,
    parse_frame,
    parse_frame_id,
    setup_stream,
)


@pytest.mark.asyncio
async def test_live_connection_catches_up_persisted_gap_and_deduplicates_realtime(monkeypatch):
    monkeypatch.setattr(svc_module, "GAP_CHECK_INTERVAL_SECONDS", 0.001)
    monkeypatch.setattr(svc_module, "PING_INTERVAL_SECONDS", 0.005)
    entry = {
        **history_entry("stdout", "persisted-after-pause", 8, event_id="50-0:0"),
        "storage_id": 12,
        "source": "pg_gap",
    }
    gap_reader = GapReader([entry], snapshot_id=12)
    broker, _, generator = setup_stream(monkeypatch, history=[], gap_reader=gap_reader)
    await drain_until_history_end(generator)

    gap_frame = await anext(generator)
    event, data = parse_frame(gap_frame)
    assert event == "log_line"
    assert data["data"]["content"] == "persisted-after-pause"
    assert parse_frame_id(gap_frame) == "pg:12"
    assert gap_reader.after_ids == [0]

    duplicate = build_log_line_message(
        "run-1",
        log_type="stdout",
        content="duplicate-event-stream-frame",
        timestamp=None,
        sequence=8,
        source="persisted_realtime",
        event_id="50-0:0",
    )
    duplicate["data"]["storage_id"] = 12
    broker.publish("run-1", duplicate)
    checkpoint = await anext(generator)
    next_event, checkpoint_data = parse_frame(checkpoint)
    assert next_event == "stream_cursor"
    assert checkpoint_data["storage_id"] == 12
    assert parse_frame_id(checkpoint) == "pg:12"
    assert parse_frame(await anext(generator))[0] == "ping"
    assert gap_reader.after_ids[-1] == 12
    await generator.aclose()


@pytest.mark.asyncio
async def test_repeated_live_event_id_is_not_sent_twice(monkeypatch):
    monkeypatch.setattr(svc_module, "PING_INTERVAL_SECONDS", 0.001)
    broker, _, generator = setup_stream(monkeypatch, history=[])
    await drain_until_history_end(generator)
    message = build_log_line_message(
        "run-1",
        log_type="stdout",
        content="once",
        timestamp=None,
        sequence=0,
        source="realtime",
        event_id="60-0:0",
    )
    broker.publish("run-1", message)
    broker.publish("run-1", message)

    first = await anext(generator)
    assert parse_frame(first)[1]["data"]["content"] == "once"
    assert parse_frame_id(first) is None
    assert parse_frame(await anext(generator))[0] == "ping"
    await generator.aclose()


@pytest.mark.asyncio
async def test_gap_query_failure_is_explicit_and_terminates(monkeypatch):
    monkeypatch.setattr(svc_module, "GAP_CHECK_INTERVAL_SECONDS", 0.001)
    gap_reader = GapReader()
    gap_reader.error = RuntimeError("db unavailable")
    _, follower, generator = setup_stream(monkeypatch, history=[], gap_reader=gap_reader)
    await drain_until_history_end(generator)

    event, data = parse_frame(await anext(generator))
    assert event == "stream_error"
    assert data["code"] == "gap_check_failed"
    with pytest.raises(StopAsyncIteration):
        await anext(generator)
    follower.unfollow.assert_awaited_once_with("run-1")


@pytest.mark.asyncio
async def test_gap_catchup_rechecks_session_after_slow_snapshot(monkeypatch):
    # guard 构造 / deadline 起点 / 首次 checkpoint / gap deadline 判定 / 慢快照后重校验
    ticks = iter((0.0, 0.0, 0.2, 0.2, 1.1))
    monkeypatch.setattr(active_module, "_monotonic", lambda: next(ticks))
    monkeypatch.setattr(svc_module, "GAP_CHECK_INTERVAL_SECONDS", 0.1)
    monkeypatch.setattr(svc_module, "SESSION_RECHECK_INTERVAL_SECONDS", 1.0)
    validator = AsyncMock(return_value=False)
    monkeypatch.setattr(svc_module, "_session_still_valid", validator)
    _, _, generator = setup_stream(monkeypatch, history=[], gap_reader=GapReader())
    await drain_until_history_end(generator)

    event, data = parse_frame(await anext(generator))

    assert event == "stream_error"
    assert data["code"] == "session_revoked"
    validator.assert_awaited_once_with(7, "jti")
    await generator.aclose()


def _build_active_context(
    replay: ReplayState,
) -> tuple[ActiveLogStream, ActiveStreamContext, LiveStreamState, SimpleNamespace]:
    broker = SimpleNamespace(get_message=AsyncMock())
    deps = ActiveStreamDependencies(
        broker=broker,
        gap_reader=MagicMock(),
        terminal_status_loader=AsyncMock(),
    )
    config = ActiveStreamConfig(ping_interval=10.0, gap_check_interval=5.0, gap_check_backoff_interval=30.0)
    live_state = LiveStreamState(known_status="running")
    context = ActiveStreamContext(
        run_id="run-1",
        subscription=object(),
        replay_state=replay,
        guard=MagicMock(),
        live_state=live_state,
    )
    return ActiveLogStream(deps, config), context, live_state, broker


@pytest.mark.asyncio
async def test_duplicate_broker_frame_does_not_flip_realtime_flag():
    # C4 回归：陈旧重复重投不代表 broker 有新数据，不得翻转"broker 健康"标志，
    # 否则缺口扫描会误退避到 30s，漏发的持久化行最多延迟 30s 才被 PG 兜底扫到。
    duplicate = build_log_line_message(
        "run-1", log_type="stdout", content="dup", timestamp=None, sequence=5, source="realtime", event_id="5-0:0"
    )
    replay = ReplayState()
    replay.record_message(duplicate)  # 预先记录 -> overlaps 为真
    stream, context, live_state, broker = _build_active_context(replay)
    deadlines = _Deadlines.start(_monotonic(), stream._config)

    broker.get_message = AsyncMock(return_value=duplicate)
    frame, terminal = await stream._next_queue_frame(context, deadlines)

    assert frame is None
    assert terminal is False
    assert live_state.realtime_since_gap_check is False

    # 新鲜帧仍必须置位（回退到基础间隔的自适应逻辑不受影响）。
    fresh = build_log_line_message(
        "run-1", log_type="stdout", content="new", timestamp=None, sequence=6, source="realtime", event_id="6-0:0"
    )
    broker.get_message = AsyncMock(return_value=fresh)
    frame, terminal = await stream._next_queue_frame(context, deadlines)

    assert frame is not None
    assert live_state.realtime_since_gap_check is True


@pytest.mark.asyncio
async def test_backlog_gap_rounds_drain_broker_between_passes(monkeypatch):
    # P1-SSE-03 回归：预算截断的连续缺口轮次之间必须消费 broker 队列。
    # 此前 backlog 时把 gap deadline 设为 now 后直接 continue，队列只进
    # 不出，高吞吐下必然 overflow 断连。
    monkeypatch.setattr(svc_module, "GAP_CHECK_INTERVAL_SECONDS", 0.001)
    entries = [
        {
            **history_entry("stdout", f"gap-{storage_id}", storage_id, event_id=f"{storage_id}-0:0"),
            "storage_id": storage_id,
        }
        for storage_id in (11, 12)
    ]
    gap_reader = GapReader(entries, snapshot_id=12, lines_per_pass=1)
    broker, _, generator = setup_stream(monkeypatch, history=[], gap_reader=gap_reader)
    await drain_until_history_end(generator)

    first = await anext(generator)
    assert parse_frame(first)[1]["data"]["content"] == "gap-11"
    # 第一轮截断后仍有积压；此时入队的实时帧必须在下一轮缺口扫描前送达
    live = build_log_line_message(
        "run-1",
        log_type="stdout",
        content="queued-during-backlog",
        timestamp=None,
        sequence=99,
        source="realtime",
        event_id="99-0:0",
    )
    broker.publish("run-1", live)

    expected_lines = ["queued-during-backlog", "gap-12"]
    log_lines: list[str] = []
    while len(log_lines) < len(expected_lines):
        event, data = parse_frame(await anext(generator))
        if event == "log_line":
            log_lines.append(data["data"]["content"])

    assert log_lines == expected_lines
    assert gap_reader.after_ids == [0, 11]
    await generator.aclose()


@pytest.mark.asyncio
async def test_continuous_queue_does_not_starve_pg_gap_check(monkeypatch):
    values = iter((0.0, 0.0, 0.0, 0.0, 0.0, 0.2, 0.2, 0.2, 0.2))
    last = [0.0]

    def monotonic() -> float:
        try:
            last[0] = next(values)
        except StopIteration:
            pass
        return last[0]

    monkeypatch.setattr(active_module, "_monotonic", monotonic)
    monkeypatch.setattr(svc_module, "GAP_CHECK_INTERVAL_SECONDS", 0.1)
    gap_entry = {
        **history_entry("stdout", "gap-before-next-queued-message", 21),
        "storage_id": 21,
    }
    gap_reader = GapReader([gap_entry], snapshot_id=21)
    broker, _, generator = setup_stream(monkeypatch, history=[], gap_reader=gap_reader)
    await drain_until_history_end(generator)
    broker.publish("run-1", {"type": "run_status", "data": {"status": "running"}})
    broker.publish("run-1", {"type": "run_status", "data": {"status": "running"}})

    assert parse_frame(await anext(generator))[0] == "run_status"
    gap_frame = await anext(generator)

    assert parse_frame(gap_frame)[1]["data"]["content"] == "gap-before-next-queued-message"
    assert parse_frame_id(gap_frame) == "pg:21"
    await generator.aclose()
