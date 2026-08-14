"""Project creator filtering must distinguish no filter from no matching user."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.common.security.auth import TokenData
from antcode_web_api.routes.v1 import project as project_routes

CREATOR_INTERNAL_ID = 42


class MemoryCache:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    async def get(self, key: str) -> object | None:
        return self.values.get(key)

    async def set(self, key: str, value: object, _ttl: int) -> None:
        self.values[key] = value


def _admin() -> TokenData:
    return TokenData(
        user_id=1,
        username="admin",
        is_admin=True,
        role="admin",
        exp=datetime.now(UTC) + timedelta(minutes=5),
        session_jti="session-1",
    )


def _query(created_by: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        page=2,
        size=20,
        type=None,
        status=None,
        tag=None,
        created_by=created_by,
        search=None,
        worker_id=None,
    )


@pytest.mark.asyncio
async def test_missing_creator_filter_returns_empty_page(monkeypatch) -> None:
    lookup_user = AsyncMock(return_value=None)
    list_projects = AsyncMock()
    monkeypatch.setattr("antcode_core.common.utils.api_optimizer.api_cache", MemoryCache())
    monkeypatch.setattr(project_routes.user_service, "get_user_by_public_id", lookup_user)
    monkeypatch.setattr(project_routes.project_service, "get_projects_list", list_projects)

    response = await project_routes.get_projects_list(
        query_params=_query("missing-public-id"),
        current_user_id=1,
        current_user=_admin(),
    )

    assert response.data.items == []
    assert response.data.pagination.total == 0
    assert response.data.pagination.pages == 0
    lookup_user.assert_awaited_once_with("missing-public-id")
    list_projects.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_creator_filter_uses_internal_owner_id(monkeypatch) -> None:
    lookup_user = AsyncMock(return_value=SimpleNamespace(id=CREATOR_INTERNAL_ID))
    list_projects = AsyncMock(return_value=([], 0))
    monkeypatch.setattr("antcode_core.common.utils.api_optimizer.api_cache", MemoryCache())
    monkeypatch.setattr(project_routes.user_service, "get_user_by_public_id", lookup_user)
    monkeypatch.setattr(project_routes.project_service, "get_projects_list", list_projects)

    await project_routes.get_projects_list(
        query_params=_query("user-public-id"),
        current_user_id=1,
        current_user=_admin(),
    )

    lookup_user.assert_awaited_once_with("user-public-id")
    assert list_projects.await_args.kwargs["user_id"] == CREATOR_INTERNAL_ID
