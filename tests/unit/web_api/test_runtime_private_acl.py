from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_web_api.routes.v1 import runtimes
from antcode_web_api.routes.v1.runtime_access import ensure_runtime_access, ensure_runtime_mutation_access
from antcode_web_api.routes.v1.runtime_models import EnvUpdateRequest
from fastapi import HTTPException


def _user(username: str = "alice", *, is_admin: bool = False) -> SimpleNamespace:
    return SimpleNamespace(username=username, is_admin=is_admin)


@pytest.mark.asyncio
async def test_runtime_list_hides_other_users_private_envs(monkeypatch) -> None:
    monkeypatch.setattr(runtimes, "ensure_worker_access", AsyncMock())
    monkeypatch.setattr(
        runtimes.runtime_control_service,
        "list_envs",
        AsyncMock(
            return_value={
                "success": True,
                "data": [
                    {"name": "shared-py311", "scope": "shared", "created_by": "admin"},
                    {"name": "alice-private", "scope": "private", "created_by": "alice"},
                    {"name": "bob-private", "scope": "private", "created_by": "bob"},
                ],
            }
        ),
    )

    response = await runtimes.list_envs("worker-1", None, 1, _user())

    assert [env["name"] for env in response.data] == ["shared-py311", "alice-private"]


@pytest.mark.asyncio
async def test_runtime_detail_rejects_foreign_private_env(monkeypatch) -> None:
    monkeypatch.setattr(runtimes, "ensure_worker_access", AsyncMock())
    monkeypatch.setattr(
        runtimes.runtime_control_service,
        "get_env",
        AsyncMock(return_value={"success": True, "data": {"scope": "private", "created_by": "bob"}}),
    )

    with pytest.raises(HTTPException) as exc_info:
        await runtimes.get_env_detail("worker-1", "bob-private", 1, _user())

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["packages", "update"])
async def test_runtime_operations_authorize_before_read_or_mutation(monkeypatch, operation: str) -> None:
    monkeypatch.setattr(runtimes, "ensure_worker_access", AsyncMock())
    monkeypatch.setattr(
        runtimes.runtime_control_service,
        "get_env",
        AsyncMock(return_value={"success": True, "data": {"scope": "private", "created_by": "bob"}}),
    )
    list_packages = AsyncMock()
    update_env = AsyncMock()
    monkeypatch.setattr(runtimes.runtime_control_service, "list_packages", list_packages)
    monkeypatch.setattr(runtimes.runtime_control_service, "update_env", update_env)

    with pytest.raises(HTTPException) as exc_info:
        if operation == "packages":
            await runtimes.list_packages("worker-1", "bob-private", 1, _user())
        else:
            await runtimes.update_env_detail(
                "worker-1",
                "bob-private",
                EnvUpdateRequest(description="changed"),
                1,
                _user(),
            )

    assert exc_info.value.status_code == 403
    list_packages.assert_not_awaited()
    update_env.assert_not_awaited()


@pytest.mark.asyncio
async def test_regular_user_cannot_update_shared_runtime(monkeypatch) -> None:
    monkeypatch.setattr(runtimes, "ensure_worker_access", AsyncMock())
    monkeypatch.setattr(
        runtimes.runtime_control_service,
        "get_env",
        AsyncMock(return_value={"success": True, "data": {"scope": "shared", "created_by": "admin"}}),
    )
    update_env = AsyncMock()
    monkeypatch.setattr(runtimes.runtime_control_service, "update_env", update_env)

    with pytest.raises(HTTPException) as exc_info:
        await runtimes.update_env_detail(
            "worker-1",
            "shared-py311",
            EnvUpdateRequest(description="changed"),
            1,
            _user(),
        )

    assert exc_info.value.status_code == 403
    update_env.assert_not_awaited()


@pytest.mark.parametrize(
    ("env", "user", "allowed"),
    [
        ({"scope": "shared", "created_by": "root"}, _user(is_admin=True), True),
        ({"scope": "shared", "created_by": "root"}, _user(), False),
        ({"scope": "private", "created_by": "alice"}, _user(), True),
        ({"scope": "private", "created_by": "bob"}, _user(is_admin=True), False),
    ],
)
def test_runtime_mutation_access_is_stricter_than_read_access(env, user, allowed: bool) -> None:
    if allowed:
        ensure_runtime_mutation_access(env, user)
        return
    with pytest.raises(HTTPException) as exc_info:
        ensure_runtime_mutation_access(env, user)
    assert exc_info.value.status_code == 403


def test_runtime_access_fails_closed_for_unknown_scope() -> None:
    with pytest.raises(HTTPException) as exc_info:
        ensure_runtime_access({"created_by": "alice"}, _user())

    assert exc_info.value.status_code == 403
