from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import antcode_core.application.services.workers.spider_stats_service as service_module
import pytest
from antcode_core.application.services.workers.spider_stats_service import SpiderStatsService


class _WorkerQuery:
    def __init__(self, workers: list[SimpleNamespace]) -> None:
        self._workers = workers

    async def all(self) -> list[SimpleNamespace]:
        return self._workers


class _HeartbeatQuery:
    def __init__(self, heartbeats: list[SimpleNamespace]) -> None:
        self._heartbeats = heartbeats

    def order_by(self, field: str) -> "_HeartbeatQuery":
        assert field == "timestamp"
        return self

    async def all(self) -> list[SimpleNamespace]:
        return self._heartbeats


def _worker(spider_stats: dict | None) -> SimpleNamespace:
    metrics = {"spider_stats": spider_stats} if spider_stats is not None else {}
    return SimpleNamespace(metrics=metrics)


def _first_worker_stats() -> dict:
    return {
        "request_count": 100,
        "response_count": 80,
        "item_scraped_count": 5,
        "error_count": 20,
        "avg_latency_ms": 100.0,
        "requests_per_minute": 10.0,
        "status_codes": {"200": 70, "500": 10},
        "domain_stats": [
            {"domain": "example.com", "reqs": 80, "successRate": 80.0, "latency": 100.0},
        ],
    }


def _second_worker_stats() -> dict:
    return {
        "request_count": 50,
        "response_count": 50,
        "item_scraped_count": 10,
        "error_count": 0,
        "avg_latency_ms": 300.0,
        "requests_per_minute": 20.0,
        "status_codes": {"200": 40, "500": 10},
        "domain_stats": [
            {"domain": "example.com", "reqs": 20, "successRate": 100.0, "latency": 300.0},
        ],
    }


def _history_stats(
    *,
    requests: int,
    responses: int,
    items: int,
    errors: int,
    avg_latency: float,
    rpm: float,
) -> dict[str, Any]:
    return {
        "request_count": requests,
        "response_count": responses,
        "item_scraped_count": items,
        "error_count": errors,
        "avg_latency_ms": avg_latency,
        "requests_per_minute": rpm,
    }


def _heartbeat(worker_id: int, timestamp: datetime, stats: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(worker_id=worker_id, timestamp=timestamp, metrics={"spider_stats": stats})


def _multiworker_history() -> list[SimpleNamespace]:
    w1_first = _history_stats(requests=10, responses=8, items=2, errors=2, avg_latency=100, rpm=5)
    w2_first = _history_stats(requests=20, responses=10, items=5, errors=1, avg_latency=200, rpm=7)
    w1_second = _history_stats(requests=15, responses=12, items=3, errors=3, avg_latency=150, rpm=6)
    w2_second = _history_stats(requests=26, responses=14, items=7, errors=2, avg_latency=250, rpm=8)
    w1_third = _history_stats(
        requests=18,
        responses=14,
        items=4,
        errors=3,
        avg_latency=157.14285714285714,
        rpm=4,
    )
    return [
        _heartbeat(1, datetime(2026, 7, 30, 12, 0, 10, tzinfo=UTC), w1_first),
        _heartbeat(2, datetime(2026, 7, 30, 12, 0, 20, tzinfo=UTC), w2_first),
        _heartbeat(1, datetime(2026, 7, 30, 12, 1, 10, tzinfo=UTC), w1_second),
        _heartbeat(2, datetime(2026, 7, 30, 12, 1, 20, tzinfo=UTC), w2_second),
        _heartbeat(1, datetime(2026, 7, 30, 12, 2, 10, tzinfo=UTC), w1_third),
    ]


@pytest.mark.asyncio
async def test_cluster_stats_match_flat_frontend_contract_and_weight_domains(monkeypatch) -> None:
    workers = [_worker(_first_worker_stats()), _worker(_second_worker_stats()), _worker(None)]
    filters: list[dict[str, str]] = []

    def filter_workers(**values: str) -> _WorkerQuery:
        filters.append(values)
        return _WorkerQuery(workers)

    monkeypatch.setattr(service_module.Worker, "filter", filter_workers)

    result = await SpiderStatsService().get_cluster_spider_stats()

    assert filters == [{"status": "online"}]
    assert result == {
        "totalRequests": 150,
        "totalResponses": 130,
        "totalItemsScraped": 15,
        "totalErrors": 20,
        "avgLatencyMs": 176.92,
        "clusterRequestsPerMinute": 30.0,
        "statusCodes": {"200": 110, "500": 20},
        "domainStats": [
            {
                "domain": "example.com",
                "reqs": 100,
                "successRate": 84.0,
                "latency": 140.0,
                "status": "Critical",
            }
        ],
        "workerCount": 3,
    }


@pytest.mark.asyncio
async def test_cluster_stats_return_complete_zero_contract_without_workers(monkeypatch) -> None:
    monkeypatch.setattr(service_module.Worker, "filter", lambda **_filters: _WorkerQuery([]))

    result = await SpiderStatsService().get_cluster_spider_stats()

    assert result == {
        "totalRequests": 0,
        "totalResponses": 0,
        "totalItemsScraped": 0,
        "totalErrors": 0,
        "avgLatencyMs": 0.0,
        "clusterRequestsPerMinute": 0.0,
        "statusCodes": {},
        "domainStats": [],
        "workerCount": 0,
    }


@pytest.mark.asyncio
async def test_history_uses_per_worker_deltas_and_response_weighted_latency(monkeypatch) -> None:
    worker_filters: list[dict[str, Any]] = []
    heartbeat_filters: list[dict[str, Any]] = []

    def filter_workers(**values: Any) -> _WorkerQuery:
        worker_filters.append(values)
        return _WorkerQuery([SimpleNamespace(id=1), SimpleNamespace(id=2)])

    def filter_heartbeats(**values: Any) -> _HeartbeatQuery:
        heartbeat_filters.append(values)
        return _HeartbeatQuery(_multiworker_history())

    monkeypatch.setattr(service_module.Worker, "filter", filter_workers)
    monkeypatch.setattr(service_module.WorkerHeartbeat, "filter", filter_heartbeats)

    result = await SpiderStatsService().get_spider_stats_history(hours=2)

    assert worker_filters == [{"status": "online"}]
    assert heartbeat_filters[0]["worker_id__in"] == [1, 2]
    assert heartbeat_filters[0]["timestamp__gte"].tzinfo is UTC
    assert result == [
        {
            "timestamp": "2026-07-30T12:01:00+00:00",
            "requestCount": 11,
            "responseCount": 8,
            "itemScrapedCount": 3,
            "errorCount": 2,
            "avgLatencyMs": 312.5,
            "requestsPerMinute": 14.0,
        },
        {
            "timestamp": "2026-07-30T12:02:00+00:00",
            "requestCount": 3,
            "responseCount": 2,
            "itemScrapedCount": 1,
            "errorCount": 0,
            "avgLatencyMs": 200.0,
            "requestsPerMinute": 4.0,
        },
    ]


def test_history_counter_reset_uses_current_snapshot_and_emits_warning(monkeypatch) -> None:
    warning = MagicMock()
    monkeypatch.setattr(service_module.logger, "warning", warning)
    heartbeats = [
        _heartbeat(
            7,
            datetime(2026, 7, 30, 12, 0, tzinfo=UTC),
            _history_stats(requests=100, responses=80, items=20, errors=5, avg_latency=100, rpm=10),
        ),
        _heartbeat(
            7,
            datetime(2026, 7, 30, 12, 1, tzinfo=UTC),
            _history_stats(requests=5, responses=4, items=1, errors=0, avg_latency=50, rpm=3),
        ),
    ]

    result = SpiderStatsService()._aggregate_history(heartbeats)

    warning.assert_called_once_with(
        "Worker {} spider counters reset: {}",
        7,
        "request_count, response_count, item_scraped_count, error_count",
    )
    assert result == [
        {
            "timestamp": "2026-07-30T12:01:00+00:00",
            "requestCount": 5,
            "responseCount": 4,
            "itemScrapedCount": 1,
            "errorCount": 0,
            "avgLatencyMs": 50.0,
            "requestsPerMinute": 3.0,
        }
    ]


def test_history_exposes_latency_rollback_without_response_reset() -> None:
    heartbeats = [
        _heartbeat(
            9,
            datetime(2026, 7, 30, 12, 0, tzinfo=UTC),
            _history_stats(requests=10, responses=10, items=0, errors=0, avg_latency=100, rpm=2),
        ),
        _heartbeat(
            9,
            datetime(2026, 7, 30, 12, 1, tzinfo=UTC),
            _history_stats(requests=11, responses=11, items=0, errors=0, avg_latency=50, rpm=2),
        ),
    ]

    with pytest.raises(ValueError, match="cumulative latency decreased"):
        SpiderStatsService()._aggregate_history(heartbeats)
