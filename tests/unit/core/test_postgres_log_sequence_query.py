from datetime import UTC, datetime

import pytest
from antcode_core.application.services.logs.postgres_log_sequence_query import (
    list_persisted_log_sequences,
)
from antcode_core.application.services.logs.postgres_log_service import PostgresLogEntry
from antcode_core.domain.models.task_log import TaskLog
from tortoise import Tortoise


@pytest.mark.asyncio
async def test_sequence_query_runs_on_sqlite_and_returns_authoritative_ids(tmp_path):
    timestamp = datetime(2026, 7, 17, tzinfo=UTC)
    await Tortoise.init(
        db_url=f"sqlite://{tmp_path / 'log-sequences.sqlite3'}",
        modules={"models": ["antcode_core.domain.models.task_log"]},
    )
    await Tortoise.generate_schemas()
    try:
        await _create_log(content="second", sequence=9, timestamp=timestamp)
        await _create_log(content="first", sequence=8, timestamp=timestamp)
        requested = [
            _entry(content="first", sequence=8, timestamp=timestamp),
            _entry(content="second", sequence=9, timestamp=timestamp),
        ]

        persisted = await list_persisted_log_sequences(requested)

        assert [entry.sequence for entry in persisted] == [8, 9]
        assert [entry.storage_id for entry in persisted] == [2, 1]
    finally:
        await Tortoise.close_connections()


@pytest.mark.asyncio
async def test_sequence_query_rejects_persisted_content_from_another_request(tmp_path):
    timestamp = datetime(2026, 7, 17, tzinfo=UTC)
    await Tortoise.init(
        db_url=f"sqlite://{tmp_path / 'log-mismatch.sqlite3'}",
        modules={"models": ["antcode_core.domain.models.task_log"]},
    )
    await Tortoise.generate_schemas()
    try:
        await _create_log(content="old", sequence=8, timestamp=timestamp)

        with pytest.raises(RuntimeError, match="回读字段与请求不一致"):
            await list_persisted_log_sequences([_entry(content="new", sequence=8, timestamp=timestamp)])
    finally:
        await Tortoise.close_connections()


@pytest.mark.asyncio
async def test_sequence_query_rejects_duplicate_persisted_sequence(tmp_path):
    timestamp = datetime(2026, 7, 17, tzinfo=UTC)
    await Tortoise.init(
        db_url=f"sqlite://{tmp_path / 'log-duplicates.sqlite3'}",
        modules={"models": ["antcode_core.domain.models.task_log"]},
    )
    await Tortoise.generate_schemas()
    try:
        await _create_log(content="first", sequence=8, timestamp=timestamp)
        await _create_log(content="duplicate", sequence=8, timestamp=timestamp)

        with pytest.raises(RuntimeError, match="重复 sequence"):
            await list_persisted_log_sequences([_entry(content="first", sequence=8, timestamp=timestamp)])
    finally:
        await Tortoise.close_connections()


def _entry(*, content: str, sequence: int, timestamp: datetime) -> PostgresLogEntry:
    return PostgresLogEntry(
        run_id="run-1",
        log_type="stdout",
        content=content,
        sequence=sequence,
        timestamp=timestamp,
        level="INFO",
        source="worker_report",
    )


async def _create_log(*, content: str, sequence: int, timestamp: datetime) -> None:
    await TaskLog.create(
        run_id="run-1",
        log_type="stdout",
        content=content,
        sequence=sequence,
        timestamp=timestamp,
        level="INFO",
        source="worker_report",
    )
