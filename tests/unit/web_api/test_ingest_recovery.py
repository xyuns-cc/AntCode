"""Storage-ID keyed SSE recovery windows."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import antcode_web_api.streams.ingest_recovery as recovery_module
import pytest
from antcode_web_api.routes.v1 import log_stream as route_module
from antcode_web_api.streams.ingest_event_id import (
    IngestCursor,
    PostgresLogCursor,
    format_postgres_cursor,
    parse_ingest_cursor,
    parse_log_stream_cursor,
)
from antcode_web_api.streams.ingest_recovery import IngestRecoveryReader, RecoveryWindowError
from antcode_web_api.streams.log_stream_recovery import BoundedRecoveryReplay
from antcode_web_api.streams.log_stream_replay import ReplayState
from fastapi import FastAPI
from fastapi.testclient import TestClient

# P1-SSE-02: 单次 DB 物化行数的上界契约（回归防止悄悄回到 200 行大页）。
_MAX_FETCH_CHUNK_ROWS = 25


async def _checkpoint() -> None:
    return None


@pytest.mark.parametrize(
    "value",
    [
        "",
        "pg:0",
        "pg:01",
        "pg:-1",
        "pg:9223372036854775808",
        "1-0",
        "1-0:",
        "01-0:0",
        "1-00:0",
        "1-0:01",
        "x-0:0",
    ],
)
def test_log_cursor_rejects_noncanonical_values(value):
    with pytest.raises(ValueError):
        parse_log_stream_cursor(value)


def test_log_cursor_accepts_pg_and_legacy_formats():
    pg_cursor = parse_log_stream_cursor("pg:42")
    legacy = parse_log_stream_cursor("1720000000000-7:12")

    assert pg_cursor == PostgresLogCursor(storage_id=42)
    assert legacy == IngestCursor(message_id="1720000000000-7", batch_index=12)
    assert format_postgres_cursor(42) == "pg:42"
    assert parse_ingest_cursor("1720000000000-7:12").event_id == "1720000000000-7:12"


def test_pg_cursor_above_bigint_returns_400_before_ticket_consumption(monkeypatch):
    resolve = AsyncMock()
    monkeypatch.setattr(route_module, "resolve_stream_ticket", resolve)
    app = FastAPI()
    app.include_router(route_module.router, prefix="/api/v1/logs")

    response = TestClient(app).get("/api/v1/logs/runs/run-1/stream?ticket=t&cursor=pg:9223372036854775808")

    assert response.status_code == 400
    resolve.assert_not_awaited()


@pytest.mark.asyncio
async def test_pg_cursor_recovers_later_storage_commit_despite_lower_event_id(monkeypatch):
    rows = [_pg_row(2, "11-0:0", "committed-second")]
    _install_window(monkeypatch, snapshot_id=2, start_id=1, rows=rows)
    reader = IngestRecoveryReader()

    window = await reader.materialize_after("run-1", PostgresLogCursor(1))
    entries = [entry async for entry in reader.iter_window(window, checkpoint=_checkpoint)]

    assert window.start_id == 1
    assert window.snapshot_id == 2
    assert [(entry["storage_id"], entry["event_id"]) for entry in entries] == [(2, "11-0:0")]


@pytest.mark.asyncio
async def test_legacy_high_event_cursor_maps_to_storage_then_recovers_lower_event(monkeypatch):
    rows = [_pg_row(2, "11-0:0", "later-commit")]
    resolve = _install_window(monkeypatch, snapshot_id=2, start_id=1, rows=rows)
    cursor = parse_ingest_cursor("100-0:0")
    reader = IngestRecoveryReader()

    window = await reader.materialize_after("run-1", cursor)
    entries = [entry async for entry in reader.iter_window(window, checkpoint=_checkpoint)]

    resolve.assert_awaited_once_with("run-1", cursor, snapshot_id=2)
    assert [entry["event_id"] for entry in entries] == ["11-0:0"]


@pytest.mark.asyncio
async def test_large_window_fetches_fixed_pages_without_materializing_all_rows(monkeypatch):
    total = 1_005
    monkeypatch.setattr(recovery_module, "RECOVERY_PAGE_SIZE", 37)
    monkeypatch.setattr(recovery_module, "capture_recovery_snapshot", AsyncMock(return_value=total + 1))
    monkeypatch.setattr(recovery_module, "resolve_cursor_storage_id", AsyncMock(return_value=1))
    monkeypatch.setattr(recovery_module, "count_recovery_entries", AsyncMock(return_value=total))
    page_sizes: list[int] = []

    async def fetch(_run_id, *, after_id, snapshot_id, limit):
        last_id = min(after_id + limit, snapshot_id)
        rows = [
            _pg_row(storage_id, f"{storage_id}-0:0", f"line-{storage_id}")
            for storage_id in range(after_id + 1, last_id + 1)
        ]
        page_sizes.append(len(rows))
        return rows

    monkeypatch.setattr(recovery_module, "fetch_recovery_page", fetch)
    reader = IngestRecoveryReader()
    window = await reader.materialize_after("run-1", PostgresLogCursor(1))
    observed = 0

    async for _entry in reader.iter_window(window, checkpoint=_checkpoint):
        observed += 1

    assert observed == total
    assert max(page_sizes) == 37
    assert len(page_sizes) > 20
    assert not hasattr(window, "entries")


@pytest.mark.asyncio
async def test_recovery_byte_budget_stops_fetching_more_chunks(monkeypatch):
    # P1-SSE-02: 恢复回放先物化后记预算，单次物化必须是小块
    # （RECOVERY_PAGE_SIZE）；消费方字节预算耗尽后关闭生成器，
    # 不得再发起下一次取数。
    total = 200
    fetch_limits: list[int] = []

    async def fetch(_run_id, *, after_id, snapshot_id, limit):
        fetch_limits.append(limit)
        last_id = min(after_id + limit, snapshot_id)
        return [_pg_row(storage_id, f"{storage_id}-0:0", "x" * 64) for storage_id in range(after_id + 1, last_id + 1)]

    monkeypatch.setattr(recovery_module, "capture_recovery_snapshot", AsyncMock(return_value=total + 1))
    monkeypatch.setattr(recovery_module, "resolve_cursor_storage_id", AsyncMock(return_value=1))
    monkeypatch.setattr(recovery_module, "count_recovery_entries", AsyncMock(return_value=total))
    monkeypatch.setattr(recovery_module, "fetch_recovery_page", fetch)
    reader = IngestRecoveryReader()
    window = await reader.materialize_after("run-1", PostgresLogCursor(1))
    replay = BoundedRecoveryReplay(
        reader,
        SimpleNamespace(checkpoint=AsyncMock()),
        run_id="run-1",
        window=window,
        replay_state=ReplayState(),
        max_lines=1000,
        max_bytes=1024,
    )

    frames = [frame async for frame in replay.frames()]

    assert replay.overflowed is True
    assert 0 < len(frames) < total
    assert recovery_module.RECOVERY_PAGE_SIZE <= _MAX_FETCH_CHUNK_ROWS
    assert fetch_limits == [recovery_module.RECOVERY_PAGE_SIZE]


@pytest.mark.asyncio
async def test_pg_window_count_drift_is_explicit(monkeypatch):
    _install_window(monkeypatch, snapshot_id=2, start_id=1, rows=[_pg_row(2, "11-0:0", "one")], count=2)
    reader = IngestRecoveryReader()
    window = await reader.materialize_after("run-1", PostgresLogCursor(1))

    with pytest.raises(RecoveryWindowError, match="expected=2 actual=1"):
        [entry async for entry in reader.iter_window(window, checkpoint=_checkpoint)]


@pytest.mark.asyncio
async def test_recovery_rejects_unexpected_pg_timestamp_type(monkeypatch):
    row = _pg_row(2, "11-0:0", "line")
    row["timestamp"] = object()
    _install_window(monkeypatch, snapshot_id=2, start_id=1, rows=[row])
    reader = IngestRecoveryReader()
    window = await reader.materialize_after("run-1", PostgresLogCursor(1))

    with pytest.raises(RecoveryWindowError, match="timestamp 类型无效"):
        [entry async for entry in reader.iter_window(window, checkpoint=_checkpoint)]


@pytest.mark.asyncio
async def test_recovery_rejects_non_increasing_page_before_emitting(monkeypatch):
    _install_window(monkeypatch, snapshot_id=2, start_id=1, rows=[_pg_row(1, "11-0:0", "duplicate")])
    reader = IngestRecoveryReader()
    window = await reader.materialize_after("run-1", PostgresLogCursor(1))

    with pytest.raises(RecoveryWindowError, match="非递增"):
        [entry async for entry in reader.iter_window(window, checkpoint=_checkpoint)]


def _install_window(
    monkeypatch,
    *,
    snapshot_id: int,
    start_id: int,
    rows: list[dict],
    count: int | None = None,
) -> AsyncMock:
    resolve = AsyncMock(return_value=start_id)
    monkeypatch.setattr(recovery_module, "capture_recovery_snapshot", AsyncMock(return_value=snapshot_id))
    monkeypatch.setattr(recovery_module, "resolve_cursor_storage_id", resolve)
    monkeypatch.setattr(
        recovery_module, "count_recovery_entries", AsyncMock(return_value=len(rows) if count is None else count)
    )
    monkeypatch.setattr(recovery_module, "fetch_recovery_page", AsyncMock(side_effect=[rows, []]))
    return resolve


def _pg_row(storage_id: int, event_id: str | None, content: str) -> dict:
    return {
        "id": storage_id,
        "event_id": event_id,
        "run_id": "run-1",
        "log_type": "stdout",
        "content": content,
        "sequence": storage_id,
        "timestamp": "2026-07-17T00:00:00+00:00",
    }
