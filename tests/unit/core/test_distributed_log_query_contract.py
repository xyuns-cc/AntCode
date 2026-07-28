import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

module = importlib.import_module("antcode_core.application.services.workers.distributed_log_service")


@pytest.mark.asyncio
async def test_historical_log_query_passes_log_type_by_keyword(monkeypatch):
    list_entries = AsyncMock(
        return_value=[
            SimpleNamespace(content="first"),
            SimpleNamespace(content="second"),
        ]
    )
    monkeypatch.setattr(module.postgres_task_log_service, "list_entries", list_entries)
    service = module.DistributedLogService(SimpleNamespace(allocate=AsyncMock()))

    lines = await service.get_logs("run-001", log_type="stderr", tail=2)

    assert lines == ["first", "second"]
    list_entries.assert_awaited_once_with(
        "run-001",
        log_type="stderr",
        limit=module.MAX_CACHE_LINES,
    )
