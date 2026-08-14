import importlib
from unittest.mock import AsyncMock, call

import pytest
from antcode_core.application.services.logs.log_cleanup_service import CleanupResult, LogCleanupService
from antcode_core.common.config import settings

cleanup_module = importlib.import_module("antcode_core.application.services.logs.log_cleanup_service")
DEFAULT_HEARTBEAT_RETENTION_DAYS = 30
EXPECTED_HEARTBEAT_ROWS_DELETED = 4
EXPECTED_BATCHED_ROWS_DELETED = 3
EXPECTED_BATCH_CALLS = 2
EXPECTED_OUTBOX_ROWS_DELETED = 5


def test_cleanup_result_defaults():
    result = CleanupResult()
    assert result.postgres_rows_deleted == 0
    assert result.worker_heartbeat_rows_deleted == 0
    assert result.scheduler_outbox_rows_deleted == 0
    assert result.redis_streams_checked == 0
    assert result.redis_streams_trimmed == 0
    assert result.redis_streams_expired == 0
    assert result.errors == []


def test_redis_patterns_cover_realtime_and_chunk_streams():
    service = LogCleanupService()
    patterns = service._redis_patterns()
    assert len(patterns) == 2
    assert all(pattern for pattern, _maxlen, _ttl in patterns)


def test_log_stream_settings_present():
    assert settings.LOG_STREAM_MAXLEN > 0
    assert settings.LOG_STREAM_TTL_SECONDS > 0
    assert settings.WORKER_HEARTBEAT_RETENTION_DAYS == DEFAULT_HEARTBEAT_RETENTION_DAYS
    assert settings.SCHEDULER_OUTBOX_RETENTION_DAYS == DEFAULT_HEARTBEAT_RETENTION_DAYS


@pytest.mark.asyncio
async def test_cleanup_includes_worker_heartbeat_retention() -> None:
    service = LogCleanupService()
    service._cleanup_postgres_logs = AsyncMock(return_value=1)
    service._cleanup_table = AsyncMock(side_effect=[2, 3, 4, EXPECTED_OUTBOX_ROWS_DELETED, 6])
    service._cleanup_redis_streams = AsyncMock()

    result = await service.cleanup_now()

    assert result.worker_heartbeat_rows_deleted == EXPECTED_HEARTBEAT_ROWS_DELETED
    assert result.scheduler_outbox_rows_deleted == EXPECTED_OUTBOX_ROWS_DELETED
    assert service._cleanup_table.await_args_list == [
        call(table="audit_logs", time_column="created_at", retention_days=service._audit_retention_days),
        call(table="worker_events", time_column="created_at", retention_days=service._worker_event_retention_days),
        call(
            table="worker_heartbeats",
            time_column="timestamp",
            retention_days=settings.WORKER_HEARTBEAT_RETENTION_DAYS,
        ),
        call(
            table="scheduler_outbox",
            time_column="consumed_at",
            retention_days=settings.SCHEDULER_OUTBOX_RETENTION_DAYS,
            require_non_null=True,
        ),
        call(table="user_sessions", time_column="expires_at", retention_days=service._session_retention_days),
    ]


@pytest.mark.asyncio
async def test_worker_heartbeat_cleanup_uses_bounded_batches(monkeypatch) -> None:
    service = LogCleanupService()
    service._batch_limit = 2
    connection = AsyncMock()
    connection.execute_query.side_effect = [(2, []), (1, [])]
    monkeypatch.setattr(cleanup_module.connections, "get", lambda _name: connection)
    monkeypatch.setattr(cleanup_module.asyncio, "sleep", AsyncMock())

    deleted = await service._cleanup_table(
        table="worker_heartbeats",
        time_column="timestamp",
        retention_days=DEFAULT_HEARTBEAT_RETENTION_DAYS,
    )

    assert deleted == EXPECTED_BATCHED_ROWS_DELETED
    assert connection.execute_query.await_count == EXPECTED_BATCH_CALLS
    sql = connection.execute_query.await_args_list[0].args[0]
    assert 'DELETE FROM "worker_heartbeats"' in sql
    assert '"timestamp" < $1' in sql
    assert "LIMIT 2" in sql


@pytest.mark.asyncio
async def test_outbox_cleanup_only_targets_consumed_rows(monkeypatch) -> None:
    service = LogCleanupService()
    connection = AsyncMock()
    connection.execute_query.return_value = (0, [])
    monkeypatch.setattr(cleanup_module.connections, "get", lambda _name: connection)

    await service._cleanup_table(
        table="scheduler_outbox",
        time_column="consumed_at",
        retention_days=settings.SCHEDULER_OUTBOX_RETENTION_DAYS,
        require_non_null=True,
    )

    sql = connection.execute_query.await_args.args[0]
    assert '"consumed_at" IS NOT NULL' in sql
    assert '"consumed_at" < $1' in sql
