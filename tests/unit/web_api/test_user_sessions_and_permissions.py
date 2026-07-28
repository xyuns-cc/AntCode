from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.common.security.auth import get_current_super_admin
from antcode_core.common.security.permissions import Permission
from antcode_core.domain.models import UserRole
from antcode_web_api.routes.v1 import base, user_sessions, users
from fastapi import HTTPException
from starlette.requests import Request


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [], "client": ("127.0.0.1", 1234)})


def test_kick_route_requires_super_admin_dependency() -> None:
    route = next(route for route in user_sessions.router.routes if route.path == "/{user_id}/kick")
    dependencies = [dependency.call for dependency in route.dependant.dependencies]
    assert get_current_super_admin in dependencies


@pytest.mark.asyncio
async def test_permissions_returns_exact_role_permissions(monkeypatch) -> None:
    user = SimpleNamespace(role=UserRole.ADMIN)
    monkeypatch.setattr(base.user_service, "get_user_by_id", AsyncMock(return_value=user))

    response = await base.get_permissions(SimpleNamespace(user_id=7))

    permissions = set(response.data["permissions"])
    assert Permission.USER_WRITE.value in permissions
    assert Permission.USER_DELETE.value not in permissions
    assert "admin" not in permissions


@pytest.mark.asyncio
async def test_super_admin_can_revoke_target_sessions(monkeypatch) -> None:
    target = SimpleNamespace(id=9, public_id="user-9", username="alice", role=UserRole.USER)
    admin = SimpleNamespace(id=1, username="root")
    revoke = AsyncMock(return_value=3)
    audit = AsyncMock()
    monkeypatch.setattr(user_sessions.user_service, "get_user_by_public_id", AsyncMock(return_value=target))
    monkeypatch.setattr(user_sessions.user_service, "get_user_by_id", AsyncMock(return_value=admin))
    monkeypatch.setattr(user_sessions.user_service, "revoke_all_sessions", revoke)
    monkeypatch.setattr(user_sessions.audit_service, "log_user_action", audit)

    response = await user_sessions.revoke_user_sessions(
        "user-9",
        _request(),
        current_admin=SimpleNamespace(user_id=1),
    )

    assert response.data == {"revoked_sessions": 3}
    revoke.assert_awaited_once_with(9)
    assert audit.await_args.kwargs["new_value"] == {"revoked_sessions": 3}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target", "status_code"),
    [
        (SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN), 400),
        (SimpleNamespace(id=2, role=UserRole.SUPER_ADMIN), 403),
    ],
)
async def test_kick_rejects_self_and_super_admin(monkeypatch, target, status_code: int) -> None:
    monkeypatch.setattr(user_sessions.user_service, "get_user_by_public_id", AsyncMock(return_value=target))

    with pytest.raises(HTTPException) as exc_info:
        await user_sessions.revoke_user_sessions(
            "target",
            _request(),
            current_admin=SimpleNamespace(user_id=1),
        )

    assert exc_info.value.status_code == status_code


@pytest.mark.asyncio
@pytest.mark.parametrize("is_active", [False, True])
async def test_admin_cannot_batch_change_super_admin_status(monkeypatch, is_active: bool) -> None:
    target = SimpleNamespace(id=2, public_id="root-2", is_admin=True)
    query = SimpleNamespace()
    query.only = lambda *_fields: SimpleNamespace(all=AsyncMock(return_value=[target]))
    update = AsyncMock()
    query.update = update
    monkeypatch.setattr(users.User, "filter", lambda **_filters: query)

    with pytest.raises(HTTPException) as exc_info:
        await users.batch_update_status(
            {"user_ids": ["root-2"], "is_active": is_active},
            current_admin=SimpleNamespace(user_id=1, is_super_admin=False),
        )

    assert exc_info.value.status_code == 403
    update.assert_not_awaited()


def test_super_admin_can_batch_change_admin_status() -> None:
    target = SimpleNamespace(id=2, is_admin=True)

    users._ensure_batch_status_permission(
        [target],
        SimpleNamespace(user_id=1, is_super_admin=True),
    )
