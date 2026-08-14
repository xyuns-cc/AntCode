from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import antcode_core.application.services.crawl.batch_service as batch_module
import pytest
from antcode_core.application.services.crawl.batch_service import CrawlBatchService

PROJECT_INTERNAL_ID = 7


class _Transaction:
    def __init__(self, connection: object) -> None:
        self._connection = connection

    async def __aenter__(self) -> object:
        return self._connection

    async def __aexit__(self, *_args) -> None:
        return None


@pytest.mark.asyncio
async def test_create_batch_locks_project_and_creates_on_same_connection(monkeypatch) -> None:
    connection = object()
    query = MagicMock()
    query.using_db.return_value = query
    query.select_for_update.return_value = query
    query.only.return_value = query
    query.first = AsyncMock(return_value=SimpleNamespace(id=PROJECT_INTERNAL_ID))
    create = AsyncMock(return_value=SimpleNamespace(public_id="batch-1"))
    monkeypatch.setattr(batch_module, "in_transaction", lambda _name: _Transaction(connection))
    monkeypatch.setattr(batch_module.Project, "filter", MagicMock(return_value=query))
    monkeypatch.setattr(batch_module.CrawlBatch, "create", create)

    batch = await CrawlBatchService().create_batch(
        "project-1",
        "batch",
        ["https://example.test"],
        1,
    )

    assert batch.public_id == "batch-1"
    query.using_db.assert_called_once_with(connection)
    query.select_for_update.assert_called_once_with()
    assert create.await_args.kwargs["using_db"] is connection
    assert create.await_args.kwargs["project_id"] == PROJECT_INTERNAL_ID
