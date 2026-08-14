"""Authorization-bound response-cache contracts for project reads."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.common.security.auth import TokenData, get_current_user, jwt_auth
from antcode_core.domain.models.user import User
from antcode_core.domain.models.user_session import UserSession
from antcode_web_api.exceptions import ProjectNotFoundException
from antcode_web_api.routes.v1 import project as project_routes

USER_ID = 7
AUTHORIZATION_STATES = 2


class MemoryCache:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    async def get(self, key: str) -> object | None:
        return self.values.get(key)

    async def set(self, key: str, value: object, _ttl: int) -> None:
        self.values[key] = value


def _token(role: str, *, is_admin: bool) -> TokenData:
    return TokenData(
        user_id=USER_ID,
        username="review-user",
        is_admin=is_admin,
        role=role,
        exp=datetime.now(UTC) + timedelta(minutes=5),
        session_jti="session-1",
    )


def _query() -> SimpleNamespace:
    return SimpleNamespace(
        page=1,
        size=20,
        type=None,
        status=None,
        tag=None,
        created_by=None,
        search=None,
        worker_id=None,
    )


@pytest.mark.asyncio
async def test_project_list_cache_does_not_survive_admin_demotion(monkeypatch) -> None:
    cache = MemoryCache()
    list_projects = AsyncMock(
        side_effect=[([SimpleNamespace(scope="global")], 1), ([SimpleNamespace(scope="owner")], 1)]
    )
    monkeypatch.setattr("antcode_core.common.utils.api_optimizer.api_cache", cache)
    monkeypatch.setattr(project_routes.project_service, "get_projects_list", list_projects)
    monkeypatch.setattr(
        project_routes.ProjectResponseBuilder,
        "build_list",
        lambda projects: [project.scope for project in projects],
    )

    admin_response = await project_routes.get_projects_list(
        query_params=_query(),
        current_user_id=USER_ID,
        current_user=_token("admin", is_admin=True),
    )
    user_response = await project_routes.get_projects_list(
        query_params=_query(),
        current_user_id=USER_ID,
        current_user=_token("user", is_admin=False),
    )

    assert admin_response.data.items == ["global"]
    assert user_response.data.items == ["owner"]
    assert list_projects.await_args_list[0].kwargs["user_id"] is None
    assert list_projects.await_args_list[1].kwargs["user_id"] == USER_ID


@pytest.mark.asyncio
async def test_old_admin_jwt_uses_live_role_before_project_cache_lookup(monkeypatch) -> None:
    cache = MemoryCache()
    role_state = {"role": "admin", "is_admin": True}
    old_admin_claims = _token("admin", is_admin=True)
    list_projects = AsyncMock(
        side_effect=[([SimpleNamespace(scope="global")], 1), ([SimpleNamespace(scope="owner")], 1)]
    )

    class SessionQuery:
        async def first(self):
            return SimpleNamespace(expires_at=datetime.now(UTC) + timedelta(minutes=5))

    async def get_live_user(**_filters):
        return SimpleNamespace(
            username="review-user",
            role=role_state["role"],
            is_admin=role_state["is_admin"],
            is_active=True,
        )

    monkeypatch.setattr(jwt_auth, "verify_token", lambda _token: old_admin_claims)
    monkeypatch.setattr(UserSession, "filter", lambda **_filters: SessionQuery())
    monkeypatch.setattr(User, "get_or_none", get_live_user)
    monkeypatch.setattr("antcode_core.common.utils.api_optimizer.api_cache", cache)
    monkeypatch.setattr(project_routes.project_service, "get_projects_list", list_projects)
    monkeypatch.setattr(
        project_routes.ProjectResponseBuilder,
        "build_list",
        lambda projects: [project.scope for project in projects],
    )

    current_user = await get_current_user(SimpleNamespace(credentials="old-admin-jwt"))
    await project_routes.get_projects_list(
        query_params=_query(),
        current_user_id=USER_ID,
        current_user=current_user,
    )
    role_state.update(role="user", is_admin=False)
    demoted_user = await get_current_user(SimpleNamespace(credentials="old-admin-jwt"))
    response = await project_routes.get_projects_list(
        query_params=_query(),
        current_user_id=USER_ID,
        current_user=demoted_user,
    )

    assert response.data.items == ["owner"]
    assert demoted_user.is_admin is False
    assert list_projects.await_count == AUTHORIZATION_STATES


@pytest.mark.asyncio
async def test_project_detail_cache_does_not_bypass_post_demotion_acl(monkeypatch) -> None:
    cache = MemoryCache()
    get_project = AsyncMock(side_effect=[SimpleNamespace(public_id="other-project"), None])
    monkeypatch.setattr("antcode_core.common.utils.api_optimizer.api_cache", cache)
    monkeypatch.setattr(project_routes.project_service, "get_project_by_id", get_project)
    monkeypatch.setattr(
        project_routes,
        "create_project_response",
        lambda project: {"id": project.public_id},
    )
    monkeypatch.setattr(project_routes, "_attach_project_detail_info", AsyncMock())

    admin_response = await project_routes.get_project_detail(
        project_id="other-project",
        current_user_id=USER_ID,
        current_user=_token("admin", is_admin=True),
    )

    with pytest.raises(ProjectNotFoundException):
        await project_routes.get_project_detail(
            project_id="other-project",
            current_user_id=USER_ID,
            current_user=_token("user", is_admin=False),
        )

    assert admin_response.data == {"id": "other-project"}
    assert get_project.await_count == AUTHORIZATION_STATES


@pytest.mark.asyncio
async def test_project_cache_scope_failure_is_not_silently_ignored(monkeypatch) -> None:
    cache = MemoryCache()
    monkeypatch.setattr("antcode_core.common.utils.api_optimizer.api_cache", cache)

    with pytest.raises(KeyError, match="current_user"):
        await project_routes.get_projects_list(query_params=_query(), current_user_id=USER_ID)

    assert cache.values == {}
