"""Database-filtered Crawl batch dispatcher queries."""

from unittest.mock import AsyncMock

import antcode_core.application.services.crawl.batch_dispatcher_service as dispatcher_module
import pytest
from antcode_core.application.services.crawl.batch_dispatcher_service import CrawlBatchDispatcherService


@pytest.fixture
def connection(monkeypatch):
    value = AsyncMock()
    monkeypatch.setattr(dispatcher_module.Tortoise, "get_connection", lambda _name: value)
    return value


@pytest.mark.asyncio
async def test_dispatched_urls_are_filtered_in_postgres(connection):
    connection.execute_query_dict.return_value = [
        {"seed_url": "https://a.test"},
        {"seed_url": "https://b.test"},
    ]

    result = await CrawlBatchDispatcherService()._already_dispatched_urls("batch-1")

    assert result == {"https://a.test", "https://b.test"}
    query, values = connection.execute_query_dict.await_args.args
    assert "result_data->>'crawl_batch_id' = $1" in query
    assert values == ["batch-1"]


@pytest.mark.asyncio
async def test_active_runs_are_filtered_by_batch_and_status_in_postgres(connection):
    connection.execute_query_dict.return_value = [{"run_id": "run-1"}]

    result = await CrawlBatchDispatcherService()._active_run_ids_for_batch("batch-1")

    assert result == ["run-1"]
    query, values = connection.execute_query_dict.await_args.args
    assert "status IN ($2, $3, $4, $5)" in query
    assert values[0] == "batch-1"
    assert values[1:] == ["pending", "dispatching", "queued", "running"]
