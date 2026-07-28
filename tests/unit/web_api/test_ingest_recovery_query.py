"""Recovery cursor mapping and keyset queries on the real SQLite dialect."""

from datetime import UTC, datetime

import pytest
from antcode_core.domain.models.task_log import TaskLog
from antcode_web_api.streams.ingest_event_id import PostgresLogCursor, parse_ingest_cursor
from antcode_web_api.streams.ingest_recovery_query import (
    RecoveryCursorUnavailableError,
    capture_recovery_snapshot,
    count_recovery_entries,
    fetch_recovery_page,
    resolve_cursor_storage_id,
)
from tortoise import Tortoise


@pytest.mark.asyncio
async def test_sqlite_recovery_follows_commit_order_and_maps_legacy_cursor(tmp_path):
    await _init_sqlite(tmp_path)
    try:
        await _create("100-0:0", "committed-first", sequence=1)
        await _create("11-0:0", "committed-second", sequence=2)
        await _create(None, "http-third", sequence=3)
        snapshot = await capture_recovery_snapshot("run-1")

        pg_start = await resolve_cursor_storage_id("run-1", PostgresLogCursor(1), snapshot_id=snapshot)
        legacy_start = await resolve_cursor_storage_id("run-1", parse_ingest_cursor("100-0:0"), snapshot_id=snapshot)
        count = await count_recovery_entries("run-1", lower_id=pg_start, snapshot_id=snapshot)
        first = await fetch_recovery_page("run-1", after_id=pg_start, snapshot_id=snapshot, limit=1)
        second = await fetch_recovery_page("run-1", after_id=first[-1]["id"], snapshot_id=snapshot, limit=10)

        assert pg_start == legacy_start == 1
        assert count == 2
        assert [(row["id"], row["event_id"]) for row in first + second] == [
            (2, "11-0:0"),
            (3, None),
        ]
    finally:
        await Tortoise.close_connections()


@pytest.mark.asyncio
async def test_missing_or_foreign_cursor_is_explicit(tmp_path):
    await _init_sqlite(tmp_path)
    try:
        await _create("10-0:0", "other-run", sequence=1, run_id="other-run")
        snapshot = await capture_recovery_snapshot("run-1")

        with pytest.raises(RecoveryCursorUnavailableError, match="无法映射"):
            await resolve_cursor_storage_id("run-1", parse_ingest_cursor("10-0:0"), snapshot_id=snapshot)
        with pytest.raises(RecoveryCursorUnavailableError, match="无法映射"):
            await resolve_cursor_storage_id("run-1", PostgresLogCursor(1), snapshot_id=snapshot)
    finally:
        await Tortoise.close_connections()


async def _init_sqlite(tmp_path) -> None:
    await Tortoise.init(
        db_url=f"sqlite://{tmp_path / 'recovery.sqlite3'}",
        modules={"models": ["antcode_core.domain.models.task_log"]},
    )
    await Tortoise.generate_schemas()


async def _create(
    event_id: str | None,
    content: str,
    *,
    sequence: int,
    run_id: str = "run-1",
) -> None:
    await TaskLog.create(
        event_id=event_id,
        run_id=run_id,
        log_type="stdout",
        content=content,
        sequence=sequence,
        timestamp=datetime(2026, 7, 17, tzinfo=UTC),
        level="INFO",
        source="worker",
    )
