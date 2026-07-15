"""IngestLogFollower：历史读取归一化、跟随计数、架构约束。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import antcode_core.application.services.logs.postgres_log_service as pg_module
import antcode_web_api.streams.ingest_follower as follower_module
import pytest
from antcode_web_api.streams.ingest_follower import IngestLogFollower


@pytest.mark.asyncio
async def test_fetch_history_normalizes_pg_entries(monkeypatch):
    follower = IngestLogFollower(namespace="ac")
    entry = SimpleNamespace(
        log_type="stdout",
        content="hello",
        timestamp=None,
        sequence=1,
    )
    monkeypatch.setattr(pg_module.postgres_log_service, "list_entries", AsyncMock(return_value=[entry]))

    history = await follower.fetch_history("run-1")

    assert history == [
        {
            "log_type": "stdout",
            "content": "hello",
            "timestamp": "",
            "sequence": 1,
            "source": "pg_history",
        }
    ]


@pytest.mark.asyncio
async def test_fetch_history_falls_back_to_per_run_stream_when_pg_empty(monkeypatch):
    follower = IngestLogFollower(namespace="ac")
    monkeypatch.setattr(pg_module.postgres_log_service, "list_entries", AsyncMock(return_value=[]))
    legacy_entries = [{"log_type": "stdout", "content": "legacy", "timestamp": "", "sequence": None}]
    monkeypatch.setattr(
        follower,
        "_fetch_history_from_per_run_stream",
        AsyncMock(return_value=legacy_entries),
    )

    history = await follower.fetch_history("run-1")

    assert history == legacy_entries


@pytest.mark.asyncio
async def test_follow_unfollow_refcounts_shared_ingest_task(monkeypatch):
    follower = IngestLogFollower(namespace="ac")
    ensure = AsyncMock()
    stop = AsyncMock()
    monkeypatch.setattr(follower, "_ensure_ingest_task", ensure)
    monkeypatch.setattr(follower, "_stop_ingest_task", stop)

    await follower.follow("run-1")
    await follower.follow("run-1")
    assert follower._follow_counts["run-1"] == 2

    await follower.unfollow("run-1")
    stop.assert_not_awaited()

    await follower.unfollow("run-1")
    assert "run-1" not in follower._follow_counts
    stop.assert_awaited_once()


def test_ingest_follower_has_no_object_storage_history_reader():
    source = follower_module.__loader__.get_source(follower_module.__name__)

    assert "_send_history_from_s3" not in source
    assert "get_log_storage" not in source
    assert "presigned" not in source.lower()
    assert "aiohttp" not in source
