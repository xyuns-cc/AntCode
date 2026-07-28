from datetime import UTC, datetime

import pytest
from antcode_core.application.services.logs.postgres_log_event_query import (
    list_persisted_log_events,
)
from antcode_core.domain.models.task_log import TaskLog
from tortoise import Tortoise


@pytest.mark.asyncio
async def test_event_query_runs_on_sqlite_and_restores_request_order(tmp_path):
    timestamp = datetime(2026, 7, 17, tzinfo=UTC)
    await Tortoise.init(
        db_url=f"sqlite://{tmp_path / 'log-events.sqlite3'}",
        modules={"models": ["antcode_core.domain.models.task_log"]},
    )
    await Tortoise.generate_schemas()
    try:
        await _create_log(
            "2-0:1",
            content="second",
            sequence=9,
            timestamp=timestamp,
            log_type="stderr",
        )
        await _create_log(
            "2-0:0",
            content="first",
            sequence=8,
            timestamp=timestamp,
        )

        entries = await list_persisted_log_events(["2-0:0", "2-0:1"])

        assert [(entry.event_id, entry.sequence) for entry in entries] == [
            ("2-0:0", 8),
            ("2-0:1", 9),
        ]
        assert [entry.storage_id for entry in entries] == [2, 1]
    finally:
        await Tortoise.close_connections()


@pytest.mark.asyncio
async def test_event_query_fails_when_persisted_row_is_missing(monkeypatch):
    query = type("_Query", (), {"values": lambda self, *_fields: _empty_rows()})()
    monkeypatch.setattr(TaskLog, "filter", lambda **_kwargs: query)

    with pytest.raises(RuntimeError, match="3-0:0"):
        await list_persisted_log_events(["3-0:0"])


async def _empty_rows():
    return []


async def _create_log(
    event_id: str,
    *,
    content: str,
    sequence: int,
    timestamp: datetime,
    log_type: str = "stdout",
) -> None:
    await TaskLog.create(
        event_id=event_id,
        run_id="run-1",
        log_type=log_type,
        content=content,
        sequence=sequence,
        timestamp=timestamp,
        level="ERROR" if log_type == "stderr" else "INFO",
        source="worker-1",
    )
