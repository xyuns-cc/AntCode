from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from antcode_core.application.services.monitoring.history_query import WorkerHistoryPageQuery
from antcode_core.application.services.monitoring.monitoring_service import MonitoringService
from antcode_web_api.routes.v1.monitoring import _history_window
from fastapi import HTTPException

TOTAL_RECORDS = 42
PAGE_NUMBER = 3
PAGE_SIZE = 20
EXPECTED_OFFSET = 40


class _HistoryQuerySet:
    def __init__(self) -> None:
        self.order = ""
        self.offset_value = 0
        self.limit_value = 0

    async def count(self) -> int:
        return TOTAL_RECORDS

    def order_by(self, order: str):
        self.order = order
        return self

    def offset(self, value: int):
        self.offset_value = value
        return self

    def limit(self, value: int):
        self.limit_value = value
        return self

    async def values(self):
        return [{"timestamp": 2}, {"timestamp": 1}]


def test_history_window_rejects_unbounded_span() -> None:
    end = datetime.now(UTC)

    with pytest.raises(HTTPException, match="30 天"):
        _history_window(end - timedelta(days=31), end)


@pytest.mark.asyncio
async def test_worker_history_uses_database_pagination(monkeypatch) -> None:
    queryset = _HistoryQuerySet()
    model = SimpleNamespace(filter=MagicMock(return_value=queryset))
    module = __import__(
        "antcode_core.application.services.monitoring.monitoring_service",
        fromlist=["WorkerPerformanceHistory"],
    )
    monkeypatch.setattr(module, "WorkerPerformanceHistory", model)
    now = datetime.now(UTC)
    query = WorkerHistoryPageQuery(
        now - timedelta(hours=1),
        now,
        "performance",
        page=PAGE_NUMBER,
        size=PAGE_SIZE,
    )

    records, total = await MonitoringService().get_worker_history("worker-1", query)

    assert total == TOTAL_RECORDS
    assert records == [{"timestamp": 1}, {"timestamp": 2}]
    assert queryset.order == "-timestamp"
    assert queryset.offset_value == EXPECTED_OFFSET
    assert queryset.limit_value == PAGE_SIZE
