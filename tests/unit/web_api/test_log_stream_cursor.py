"""SSE cursor replay ordering and overlap tests."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import antcode_web_api.streams.log_stream_service as service_module
import pytest
from antcode_web_api.routes.v1 import log_stream as route_module
from antcode_web_api.streams.ingest_event_id import parse_ingest_cursor
from antcode_web_api.streams.log_stream_replay import recovery_frame_size
from antcode_web_api.streams.log_stream_service import LogStreamService
from antcode_web_api.streams.sse import build_log_line_message
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.unit.web_api.fake_stream_capacity import make_broker


def _entry(content: str, event_id: str, *, sequence: int, storage_id: int) -> dict:
    return {
        "log_type": "stdout",
        "content": content,
        "timestamp": "2026-07-17T00:00:00+00:00",
        "sequence": sequence,
        "source": "pg_recovery",
        "event_id": event_id,
        "storage_id": storage_id,
    }


def _frame(frame: bytes) -> tuple[str, dict, str | None]:
    lines = frame.decode().splitlines()
    event = next(line.removeprefix("event: ") for line in lines if line.startswith("event: "))
    data = json.loads(next(line.removeprefix("data: ") for line in lines if line.startswith("data: ")))
    event_id = next((line.removeprefix("id: ") for line in lines if line.startswith("id: ")), None)
    return event, data, event_id


def _setup(monkeypatch, *, recovered: list[dict], start_id: int = 9, snapshot_id: int = 11):
    checkpoints: list[int] = []
    broker = make_broker()

    async def materialize(_run_id, _cursor):
        return SimpleNamespace(start_id=start_id, snapshot_id=snapshot_id, entries=recovered)

    async def iter_window(window, *, checkpoint):
        for entry in window.entries:
            await checkpoint()
            checkpoints.append(entry["storage_id"])
            yield entry

    history_reader = SimpleNamespace(plan_latest=AsyncMock())
    follower = SimpleNamespace(
        follow=AsyncMock(),
        unfollow=AsyncMock(),
        history_reader=history_reader,
    )
    recovery = SimpleNamespace(
        materialize_after=AsyncMock(side_effect=materialize),
        iter_window=iter_window,
    )
    monkeypatch.setattr(service_module, "run_stream_broker", broker)
    monkeypatch.setattr(service_module, "ingest_log_follower", follower)
    monkeypatch.setattr(service_module, "ingest_recovery_reader", recovery)
    execution = SimpleNamespace(status=SimpleNamespace(value="running"))
    generator = LogStreamService().stream(
        "run-1",
        execution,
        user_id=7,
        session_jti="jti",
        cursor=parse_ingest_cursor("9-0:0"),
    )
    return checkpoints, history_reader, broker, generator


@pytest.mark.asyncio
async def test_recovery_replays_fixed_pg_window_and_deduplicates_all_boundaries(monkeypatch):
    recovered = [
        _entry("hot", "10-0:1", sequence=0, storage_id=10),
        _entry("persisted", "11-0:0", sequence=0, storage_id=11),
    ]
    checkpoints, history_reader, broker, generator = _setup(monkeypatch, recovered=recovered)

    assert _frame(await anext(generator))[0] == "run_status"
    broker.publish("run-1", _live("queued-duplicate", "10-0:1", storage_id=10))
    broker.publish("run-1", _live("queued-new", "12-0:0", storage_id=12))

    hot_frame = _frame(await anext(generator))
    persisted_frame = _frame(await anext(generator))
    checkpoint_frame = _frame(await anext(generator))
    recovery_complete_frame = _frame(await anext(generator))
    realtime_frame = _frame(await anext(generator))

    assert (hot_frame[1]["data"]["content"], hot_frame[2]) == ("hot", "pg:10")
    assert (persisted_frame[1]["data"]["content"], persisted_frame[2]) == ("persisted", "pg:11")
    assert (checkpoint_frame[0], checkpoint_frame[2]) == ("stream_cursor", "pg:11")
    assert recovery_complete_frame[0] == "recovery_complete"
    assert recovery_complete_frame[1]["recovered_lines"] == len(recovered)
    # 恢复窗口覆盖的实时重复帧（storage_id<=快照水位）被去重，只放行新帧
    assert (realtime_frame[1]["data"]["content"], realtime_frame[2]) == ("queued-new", None)
    # 每条恢复日志发出前都经过 guard checkpoint（会话/寿命重校验可打断分页回放）
    assert checkpoints == [10, 11]
    # 断线恢复只回放固定 PG 缺口窗口，不再重放全量历史快照
    history_reader.plan_latest.assert_not_awaited()
    await generator.aclose()


@pytest.mark.asyncio
async def test_empty_recovery_window_emits_recovery_complete(monkeypatch):
    _, _, _, generator = _setup(monkeypatch, recovered=[], start_id=9, snapshot_id=9)

    assert _frame(await anext(generator))[0] == "run_status"
    event, data, event_id = _frame(await anext(generator))

    assert event == "recovery_complete"
    assert data["recovered_lines"] == 0
    assert event_id is None
    await generator.aclose()


@pytest.mark.asyncio
async def test_partial_recovery_overflow_does_not_emit_recovery_complete(monkeypatch):
    first = _entry("first", "10-0:0", sequence=0, storage_id=10)
    second = _entry("second", "11-0:0", sequence=0, storage_id=11)
    monkeypatch.setattr(service_module, "HISTORY_MAX_BYTES", recovery_frame_size("run-1", first))
    _, _, _, generator = _setup(monkeypatch, recovered=[first, second])

    assert _frame(await anext(generator))[0] == "run_status"
    recovered_frame = _frame(await anext(generator))
    error_frame = _frame(await anext(generator))

    assert recovered_frame[0] == "log_line"
    assert recovered_frame[1]["data"]["content"] == "first"
    assert error_frame[0] == "stream_error"
    assert error_frame[1]["code"] == "recovery_overflow"
    with pytest.raises(StopAsyncIteration):
        await anext(generator)


@pytest.mark.asyncio
async def test_pg_recovery_window_covers_entry_trimmed_from_redis(monkeypatch):
    trimmed = _entry("trimmed-but-persisted", "10-0:1", sequence=10, storage_id=10)
    retained = _entry("retained-hot", "11-0:0", sequence=0, storage_id=11)
    _, _, _, generator = _setup(monkeypatch, recovered=[trimmed, retained])

    assert _frame(await anext(generator))[0] == "run_status"
    observed = []
    while len(observed) < 2:
        event, data, event_id = _frame(await anext(generator))
        if event == "log_line":
            observed.append((data["data"]["content"], event_id))

    # Redis 已裁剪的日志由 PG 恢复窗口按 storage_id 顺序补齐，不丢行
    assert observed == [("trimmed-but-persisted", "pg:10"), ("retained-hot", "pg:11")]
    await generator.aclose()


def test_bad_cursor_returns_400_before_ticket_consumption(monkeypatch):
    resolve = AsyncMock()
    monkeypatch.setattr(route_module, "resolve_stream_ticket", resolve)
    app = FastAPI()
    app.include_router(route_module.router, prefix="/api/v1/logs")

    response = TestClient(app).get("/api/v1/logs/runs/run-1/stream?ticket=t&cursor=bad")

    assert response.status_code == 400
    resolve.assert_not_awaited()


def _live(content: str, event_id: str, *, storage_id: int) -> dict:
    return build_log_line_message(
        "run-1",
        log_type="stdout",
        content=content,
        timestamp="2026-07-17T00:00:00+00:00",
        sequence=0,
        source="realtime",
        event_id=event_id,
        storage_id=storage_id,
    )
