"""PostgreSQL storage-ID catch-up reader tests (keyset 分页 + 双预算)."""

from datetime import UTC, datetime

import antcode_web_api.streams.log_stream_gap as gap_module
import pytest
from antcode_web_api.streams.log_stream_gap import (
    GAP_FETCH_CHUNK_ROWS,
    GapScanProgress,
    PostgresLogGapReader,
)


class _Connection:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, list]] = []

    async def execute_query(self, sql: str, params: list):
        self.calls.append((sql, params))
        if "MAX" in sql:
            return 1, [{"max_id": self.rows[-1]["id"] if self.rows else 0}]
        _, after_id, snapshot_id, limit = params
        page = [row for row in self.rows if after_id < row["id"] <= snapshot_id][:limit]
        return len(page), page

    def page_calls(self) -> list[list]:
        return [params for sql, params in self.calls if "ORDER BY" in sql]


def _row(storage_id: int, *, content: str | None = None) -> dict:
    return {
        "id": storage_id,
        "event_id": f"{storage_id}-0:0",
        "run_id": "run-1",
        "log_type": "stdout",
        "content": content if content is not None else f"line-{storage_id}",
        "sequence": storage_id,
        "timestamp": datetime(2026, 7, 17, tzinfo=UTC),
        "level": "INFO",
        "source": "worker",
    }


def _reader_on(monkeypatch, rows: list[dict]) -> tuple[PostgresLogGapReader, _Connection]:
    connection = _Connection(rows)
    monkeypatch.setattr(gap_module, "_connection", lambda: connection)
    return PostgresLogGapReader(), connection


async def _scan(reader: PostgresLogGapReader, window, progress: GapScanProgress) -> list[dict]:
    return [entry async for entry in reader.iter_window(window, progress)]


@pytest.mark.asyncio
async def test_gap_reader_keyset_pages_without_count_or_offset(monkeypatch):
    # P1-SSE-04: 纯 keyset 分页——不发 COUNT 也不发 OFFSET，页大小固定为小块。
    rows = [_row(index) for index in range(1, GAP_FETCH_CHUNK_ROWS * 2 + 6)]
    reader, connection = _reader_on(monkeypatch, rows)

    after_id = 2
    window = await reader.plan("run-1", after_id)
    progress = GapScanProgress()
    entries = await _scan(reader, window, progress)

    expected_pages = 3
    assert (window.after_id, window.snapshot_id) == (after_id, len(rows))
    assert [entry["storage_id"] for entry in entries] == list(range(after_id + 1, len(rows) + 1))
    assert all("COUNT" not in sql and "OFFSET" not in sql for sql, _ in connection.calls)
    page_calls = connection.page_calls()
    assert len(page_calls) == expected_pages
    assert all(params[-1] == GAP_FETCH_CHUNK_ROWS for params in page_calls)
    assert all(params[2] == window.snapshot_id for params in page_calls)
    # keyset 游标推进：每页从上一页末行 id 之后继续
    expected_cursors = [after_id + GAP_FETCH_CHUNK_ROWS * page for page in range(expected_pages)]
    assert [params[1] for params in page_calls] == expected_cursors
    assert (progress.truncated, progress.last_id) == (False, len(rows))


@pytest.mark.asyncio
async def test_gap_reader_stops_at_line_budget_and_reports_truncation(monkeypatch):
    # P1-SSE-04: 行数预算截断——剩余缺口留给下一轮，物化行数有上界。
    budget_lines = GAP_FETCH_CHUNK_ROWS + 5
    monkeypatch.setattr(gap_module, "GAP_MAX_LINES_PER_PASS", budget_lines)
    rows = [_row(index) for index in range(1, GAP_FETCH_CHUNK_ROWS * 4 + 1)]
    reader, connection = _reader_on(monkeypatch, rows)

    window = await reader.plan("run-1", 0)
    progress = GapScanProgress()
    entries = await _scan(reader, window, progress)

    assert len(entries) == budget_lines
    assert (progress.truncated, progress.last_id) == (True, budget_lines)
    # 预算命中于第二小块中途，单次 DB 物化不超过两小块
    expected_fetches = 2
    assert len(connection.page_calls()) == expected_fetches


@pytest.mark.asyncio
async def test_gap_reader_stops_at_byte_budget_before_more_fetches(monkeypatch):
    # P1-SSE-02 + P1-round6 5.3: 累计 SSE 帧字节预算截断——按真实帧字节
    # (recovery_frame_size = JSON + SSE prefix + newline)计算, 而不是只算
    # raw content;超预算即停,不再发起下一次取数。
    from antcode_web_api.streams.log_stream_replay import recovery_frame_size

    content = "x" * 10
    # 用真实帧字节 * 3 作为预算, 让前 3 行进入, 第 4 行触发截断
    sample_entry = {
        "log_type": "stdout",
        "content": content,
        "timestamp": "2026-07-17T00:00:00+00:00",
        "sequence": 1,
        "source": "pg_gap",
        "storage_id": 1,
        "event_id": "1-0:0",
    }
    frame_size = recovery_frame_size("run-1", sample_entry)
    monkeypatch.setattr(gap_module, "GAP_MAX_BYTES_PER_PASS", frame_size * 3)
    rows = [_row(index, content=content) for index in range(1, GAP_FETCH_CHUNK_ROWS * 2 + 1)]
    reader, connection = _reader_on(monkeypatch, rows)

    window = await reader.plan("run-1", 0)
    progress = GapScanProgress()
    entries = await _scan(reader, window, progress)

    assert [entry["storage_id"] for entry in entries] == [1, 2, 3]
    assert (progress.truncated, progress.last_id) == (True, 3)
    assert len(connection.page_calls()) == 1


@pytest.mark.asyncio
async def test_gap_reader_empty_gap_emits_nothing(monkeypatch):
    rows = [_row(1), _row(2)]
    reader, connection = _reader_on(monkeypatch, rows)

    window = await reader.plan("run-1", 2)
    progress = GapScanProgress()
    entries = await _scan(reader, window, progress)

    assert entries == []
    assert (progress.truncated, progress.last_id) == (False, 0)
    assert connection.page_calls() == []


@pytest.mark.asyncio
async def test_gap_reader_rejects_non_increasing_storage_ids(monkeypatch):
    reader, _ = _reader_on(monkeypatch, [_row(3), _row(3)])
    window = await reader.plan("run-1", 0)

    with pytest.raises(RuntimeError, match="非递增"):
        await _scan(reader, window, GapScanProgress())
