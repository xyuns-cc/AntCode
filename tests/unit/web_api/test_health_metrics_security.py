from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.infrastructure.resilience.health import HealthChecker, HealthStatus
from antcode_web_api.prometheus_metrics import UNMATCHED_ROUTE_LABEL, _normalize_path
from antcode_web_api.routes.v1 import base
from starlette.requests import Request

HTTP_OK = 200
RESOURCE_PROBE_COUNT = 3


def _request(*, route=None, path="/attacker/controlled/value") -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "scheme": "http",
        "server": ("test", 80),
        "client": ("test", 1234),
        "root_path": "",
    }
    if route is not None:
        scope["route"] = route
    return Request(scope)


def test_prometheus_unmatched_path_uses_bounded_label():
    assert _normalize_path(_request()) == UNMATCHED_ROUTE_LABEL


def test_prometheus_matched_path_uses_route_template():
    route = SimpleNamespace(path="/api/v1/runs/{run_id}")
    assert _normalize_path(_request(route=route)) == "/api/v1/runs/{run_id}"


@pytest.mark.asyncio
async def test_anonymous_detailed_health_skips_expensive_checks(monkeypatch):
    check_all = AsyncMock()
    monkeypatch.setattr(base.health_checker, "check_all", check_all)

    response = await base.detailed_health_check(include_details=True, current_user=None)

    assert response.status_code == HTTP_OK
    check_all.assert_not_awaited()


@pytest.mark.asyncio
async def test_system_resource_probe_runs_blocking_calls_off_loop(monkeypatch):
    checker = HealthChecker()
    to_thread = AsyncMock(
        side_effect=[
            SimpleNamespace(percent=10, available=1),
            SimpleNamespace(percent=20, free=1),
            5,
        ]
    )
    monkeypatch.setattr("antcode_core.infrastructure.resilience.health.asyncio.to_thread", to_thread)

    result = await checker._check_system_resources()

    assert result.status == HealthStatus.HEALTHY
    assert to_thread.await_count == RESOURCE_PROBE_COUNT
