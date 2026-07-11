"""PostgreSQL task log service behavior."""

import pytest
from antcode_core.application.services.logs.postgres_log_service import (
    PostgresLogEntry,
    PostgresLogService,
)


@pytest.mark.asyncio
async def test_append_entries_includes_event_id_for_deduplication(monkeypatch):
    service = PostgresLogService()
    entry = PostgresLogEntry(
        event_id="worker-log:run-1:3",
        run_id="run-1",
        log_type="stdout",
        content="hello",
        sequence=3,
        source="worker_direct",
    )

    class _Connection:
        rows = None

        async def execute_many(self, _sql, rows):
            self.rows = rows

    connection = _Connection()
    monkeypatch.setattr("tortoise.Tortoise.get_connection", lambda _name: connection)

    assert await service.append_entries([entry]) == 1
    row = connection.rows[0]

    assert row[0] == "worker-log:run-1:3"
    assert row[1] == "run-1"
