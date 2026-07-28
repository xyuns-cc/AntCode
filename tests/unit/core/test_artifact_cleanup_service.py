"""Reference-aware TTL deletion for source bundle artifacts."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from antcode_core.application.services.projects.artifact_cleanup_service import (
    ArtifactCleanupService,
)


class _FakeConn:
    def __init__(self, results):
        self.calls = []
        self._results = list(results)

    async def execute_query(self, sql, params):
        self.calls.append((sql, params))
        return self._results.pop(0)


@pytest.mark.asyncio
async def test_cleanup_deletes_chunks_and_metadata_in_single_atomic_statement():
    # P1-DB-01: chunks 与 metadata 必须在同一条语句（同一快照的 CTE 候选集）
    # 里成对删除；两条独立 DELETE 之间提交的新 snapshot 会造成
    # "有 metadata 无 chunks" 的损坏 artifact。
    fake_conn = _FakeConn(results=[(1, [{"chunks": 7, "artifacts": 3}])])

    @patch("antcode_core.application.services.projects.artifact_cleanup_service.in_transaction")
    async def _run(mock_in_tx):
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=fake_conn)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_in_tx.return_value = ctx

        service = ArtifactCleanupService()
        service._retention_days = 30
        result = await service.cleanup_now()

        assert result.chunks_deleted == 7
        assert result.artifacts_deleted == 3
        mock_in_tx.assert_called_once_with("default")

        assert len(fake_conn.calls) == 1
        sql, params = fake_conn.calls[0]
        assert "WITH doomed AS" in sql
        assert "DELETE FROM source_artifact_chunks" in sql
        assert "DELETE FROM source_artifacts" in sql
        assert "run_source_snapshots" in sql, "must skip artifacts still referenced"
        assert "NOT IN" in sql
        assert "FOR UPDATE" in sql

        cutoff = params[0]
        expected = datetime.now(UTC) - timedelta(days=30)
        assert abs((cutoff - expected).total_seconds()) < 5

    await _run()


@pytest.mark.asyncio
async def test_cleanup_uses_configured_retention_days():
    fake_conn = _FakeConn(results=[(1, [{"chunks": 0, "artifacts": 0}])])

    @patch("antcode_core.application.services.projects.artifact_cleanup_service.in_transaction")
    async def _run(mock_in_tx):
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=fake_conn)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_in_tx.return_value = ctx

        service = ArtifactCleanupService()
        service._retention_days = 90
        result = await service.cleanup_now()

        cutoff = result.cutoff
        expected = datetime.now(UTC) - timedelta(days=90)
        assert cutoff is not None
        assert abs((cutoff - expected).total_seconds()) < 5

    await _run()


@pytest.mark.asyncio
async def test_start_stop_lifecycle_safe_to_call_twice():
    service = ArtifactCleanupService()
    service._do_cleanup = AsyncMock()
    service._initial_delay_seconds = 0

    await service.start(interval_hours=24)
    await service.start(interval_hours=24)  # should be a no-op
    assert service._running is True
    await service.stop()
    assert service._running is False
