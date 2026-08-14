"""PostgreSQL serialization contract for Crawl lifecycle outbox events."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import antcode_core.application.services.crawl.batch_aggregate_lock as lock_module
import antcode_core.application.services.crawl.batch_dispatcher_service as dispatcher_module
import pytest
from antcode_core.application.services.crawl.batch_aggregate_lock import (
    crawl_batch_aggregate_lock,
)
from antcode_core.application.services.crawl.batch_dispatch_state import (
    crawl_batch_run_id,
)
from antcode_core.application.services.crawl.batch_dispatcher_service import (
    CrawlBatchDispatcherService,
)

MAX_RUN_ID_LENGTH = 64


class _AcquireConnection:
    def __init__(self, connection) -> None:
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *_args) -> None:
        return None


@pytest.mark.asyncio
async def test_aggregate_lock_uses_same_session_for_lock_and_unlock(monkeypatch) -> None:
    connection = AsyncMock()
    connection.fetchval.return_value = True
    client = AsyncMock()
    client.acquire_connection = lambda: _AcquireConnection(connection)
    monkeypatch.setattr(lock_module.Tortoise, "get_connection", lambda _name: client)

    async with crawl_batch_aggregate_lock("batch-1"):
        pass

    assert "pg_advisory_lock" in connection.execute.await_args.args[0]
    assert "pg_advisory_unlock" in connection.fetchval.await_args.args[0]
    assert connection.execute.await_args.args[1] == connection.fetchval.await_args.args[1]


@pytest.mark.asyncio
async def test_lifecycle_handlers_for_same_batch_do_not_overlap(monkeypatch) -> None:
    gate = asyncio.Lock()
    active = 0
    max_active = 0

    @asynccontextmanager
    async def aggregate_lock(_batch_id: str):
        async with gate:
            yield

    async def handler(_batch_id: str) -> None:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0)
        active -= 1

    service = CrawlBatchDispatcherService()
    monkeypatch.setattr(dispatcher_module, "crawl_batch_aggregate_lock", aggregate_lock)
    monkeypatch.setattr(service, "_on_batch_started", handler)

    await asyncio.gather(
        service.handle_batch_event("batch_started", "batch-1"),
        service.handle_batch_event("batch_started", "batch-1"),
    )

    assert max_active == 1


def test_seed_run_identity_is_deterministic_and_url_scoped() -> None:
    first = crawl_batch_run_id("batch-1", "https://a.test")
    assert first == crawl_batch_run_id("batch-1", "https://a.test")
    assert first != crawl_batch_run_id("batch-1", "https://b.test")
    assert len(first) <= MAX_RUN_ID_LENGTH
