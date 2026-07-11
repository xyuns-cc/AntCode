import pytest
from antcode_core.domain.models import Worker
from antcode_web_api.routes.v1.users import router as users_router
from antcode_web_api.routes.v1.workers import get_render_capable_workers
from antcode_web_api.routes.v1.workers import router as workers_router
from starlette.routing import Match


def _matched_path(router, path: str, method: str) -> str | None:
    scope = {
        "type": "http",
        "path": path,
        "method": method,
        "root_path": "",
    }
    for route in router.routes:
        match, _ = route.matches(scope)
        if match is Match.FULL:
            return route.path
    return None


def test_worker_static_routes_are_not_shadowed() -> None:
    assert _matched_path(workers_router, "/best", "GET") == "/best"
    assert _matched_path(workers_router, "/render-capable", "GET") == "/render-capable"


def test_user_cache_delete_is_not_shadowed() -> None:
    assert _matched_path(users_router, "/cache", "DELETE") == "/cache"


@pytest.mark.asyncio
async def test_render_workers_query_is_paginated_in_database(monkeypatch) -> None:
    query = _FakeWorkerQuery()
    monkeypatch.setattr(Worker, "filter", lambda **kwargs: query.record("filter", kwargs))

    response = await get_render_capable_workers(page=3, size=20, region="cn", current_user=None)

    assert response.data.total == 0
    assert ("offset", 40) in query.calls
    assert ("limit", 20) in query.calls


class _FakeWorkerQuery:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def record(self, name: str, value: object):
        self.calls.append((name, value))
        return self

    def filter(self, *args, **kwargs):
        return self.record("filter", (args, kwargs))

    async def count(self) -> int:
        return 0

    def offset(self, value: int):
        return self.record("offset", value)

    def limit(self, value: int):
        return self.record("limit", value)

    def __await__(self):
        async def _result():
            return []

        return _result().__await__()
