import importlib
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.logs.postgres_log_service import PostgresLogEntry
from antcode_core.infrastructure.redis.sse_event_stream import SSEEventPublishError
from antcode_master.ingester.log_line_publisher import publish_persisted_log_lines

publisher_module = importlib.import_module("antcode_master.ingester.log_line_publisher")


@pytest.mark.asyncio
async def test_publisher_uses_database_authoritative_log_identity(monkeypatch):
    publish = AsyncMock()
    monkeypatch.setattr(publisher_module, "publish_sse_event", publish)
    timestamp = datetime(2026, 7, 17, 8, 30, tzinfo=UTC)
    entry = PostgresLogEntry(
        run_id="run-1",
        log_type="stderr",
        content="failed",
        timestamp=timestamp,
        sequence=73,
        event_id="12-0:3",
        storage_id=91,
    )

    await publish_persisted_log_lines([entry])

    message = publish.await_args.args[0]
    assert message["type"] == "log_line"
    assert message["run_id"] == "run-1"
    assert message["data"]["sequence"] == 73
    assert message["data"]["event_id"] == "12-0:3"
    assert message["data"]["storage_id"] == 91
    assert message["data"]["timestamp"] == timestamp.isoformat()
    assert message["data"]["source"] == "realtime"


@pytest.mark.asyncio
async def test_publisher_rejects_log_without_event_identity(monkeypatch):
    publish = AsyncMock()
    monkeypatch.setattr(publisher_module, "publish_sse_event", publish)
    entry = PostgresLogEntry(run_id="run-1", log_type="stdout", content="line")

    with pytest.raises(RuntimeError, match="event_id"):
        await publish_persisted_log_lines([entry])

    publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_publisher_keeps_redis_failure_visible(monkeypatch):
    publish = AsyncMock(side_effect=SSEEventPublishError("redis unavailable"))
    monkeypatch.setattr(publisher_module, "publish_sse_event", publish)
    entry = PostgresLogEntry(
        run_id="run-1",
        log_type="stdout",
        content="line",
        sequence=1,
        event_id="13-0:0",
    )

    with pytest.raises(SSEEventPublishError, match="redis unavailable"):
        await publish_persisted_log_lines([entry])
