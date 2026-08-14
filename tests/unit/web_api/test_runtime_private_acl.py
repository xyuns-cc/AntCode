from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_contracts.runtime_metadata import (
    RUNTIME_DESCRIPTION_MAX_BYTES,
    RUNTIME_DESCRIPTION_MAX_LENGTH,
    RUNTIME_KEY_MAX_BYTES,
    RUNTIME_KEY_MAX_LENGTH,
)
from antcode_web_api.routes.v1 import runtimes
from antcode_web_api.routes.v1.runtime_access import ensure_runtime_access, ensure_runtime_mutation_access
from antcode_web_api.routes.v1.runtime_models import CreateEnvRequest, EnvUpdateRequest
from fastapi import HTTPException
from pydantic import ValidationError

HTTP_FORBIDDEN = 403
RENAMED_OWNER_ID = 7
REUSED_USERNAME_USER_ID = 8


@pytest.mark.parametrize(
    "payload",
    [
        {"key": "k" * (RUNTIME_KEY_MAX_LENGTH + 1)},
        {"description": "d" * (RUNTIME_DESCRIPTION_MAX_LENGTH + 1)},
    ],
)
def test_runtime_update_rejects_oversized_persistent_metadata(payload) -> None:
    with pytest.raises(ValidationError):
        EnvUpdateRequest(**payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"key": "界" * (RUNTIME_KEY_MAX_BYTES // 3 + 1)},
        {"description": "界" * (RUNTIME_DESCRIPTION_MAX_BYTES // 3 + 1)},
    ],
)
def test_runtime_update_rejects_multibyte_metadata_over_utf8_contract(payload) -> None:
    with pytest.raises(ValidationError, match="UTF-8"):
        EnvUpdateRequest(**payload)


def _user(username: str = "alice", *, user_id: int = 1, is_admin: bool = False) -> SimpleNamespace:
    return SimpleNamespace(user_id=user_id, username=username, is_admin=is_admin)


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
                    {
                        "name": "alice-private",
                        "scope": "private",
                        "created_by": "alice",
                        "owner_user_id": "1",
                    },
                    {
                        "name": "bob-private",
                        "scope": "private",
                        "created_by": "bob",
                        "owner_user_id": "2",
                    },
                ],
            }
        ),
    )

    response = await runtimes.list_envs("worker-1", None, 1, _user())

    assert [env["name"] for env in response.data] == ["shared-py311", "alice-private"]


@pytest.mark.asyncio
async def test_runtime_create_persists_stable_owner_and_audits(monkeypatch) -> None:
    worker = SimpleNamespace(public_id="worker-1")
    create = AsyncMock(return_value={"success": True, "data": {"name": "private-test"}})
    audit = AsyncMock()
    monkeypatch.setattr(runtimes, "ensure_worker_admin_access", AsyncMock(return_value=worker))
    monkeypatch.setattr(runtimes.runtime_control_service, "create_env", create)
    monkeypatch.setattr(runtimes, "audit_runtime_create", audit)
    user = _user("alice-renamed", user_id=7, is_admin=True)

    response = await runtimes.create_env(
        "worker-1",
        CreateEnvRequest(scope="private", python_version="3.11", env_name="private-test"),
        7,
        user,
    )

    assert response.data["env"] == {"name": "private-test"}
    assert create.await_args.kwargs["created_by"] == "alice-renamed"
    assert create.await_args.kwargs["owner_user_id"] == "7"
    audit.assert_awaited_once_with("worker-1", "private-test", user)


@pytest.mark.asyncio
async def test_runtime_detail_rejects_foreign_private_env(monkeypatch) -> None:
    monkeypatch.setattr(runtimes, "ensure_worker_access", AsyncMock())
    monkeypatch.setattr(
        runtimes.runtime_control_service,
        "get_env",
        AsyncMock(
            return_value={
                "success": True,
                "data": {"scope": "private", "created_by": "bob", "owner_user_id": "2"},
            }
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        await runtimes.get_env_detail("worker-1", "bob-private", 1, _user())

    assert exc_info.value.status_code == HTTP_FORBIDDEN


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["packages", "update"])
async def test_runtime_operations_authorize_before_read_or_mutation(monkeypatch, operation: str) -> None:
    monkeypatch.setattr(runtimes, "ensure_worker_access", AsyncMock())
    monkeypatch.setattr(
        runtimes.runtime_control_service,
        "get_env",
        AsyncMock(
            return_value={
                "success": True,
                "data": {"scope": "private", "created_by": "bob", "owner_user_id": "2"},
            }
        ),
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

    assert exc_info.value.status_code == HTTP_FORBIDDEN
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

    assert exc_info.value.status_code == HTTP_FORBIDDEN
    update_env.assert_not_awaited()


@pytest.mark.parametrize(
    ("env", "user", "allowed"),
    [
        ({"scope": "shared", "created_by": "root"}, _user(is_admin=True), True),
        ({"scope": "shared", "created_by": "root"}, _user(), False),
        ({"scope": "private", "created_by": "alice", "owner_user_id": "1"}, _user(), True),
        ({"scope": "private", "created_by": "bob", "owner_user_id": "2"}, _user(is_admin=True), False),
    ],
)
def test_runtime_mutation_access_is_stricter_than_read_access(env, user, allowed: bool) -> None:
    if allowed:
        ensure_runtime_mutation_access(env, user)
        return
    with pytest.raises(HTTPException) as exc_info:
        ensure_runtime_mutation_access(env, user)
    assert exc_info.value.status_code == HTTP_FORBIDDEN


def test_runtime_access_fails_closed_for_unknown_scope() -> None:
    with pytest.raises(HTTPException) as exc_info:
        ensure_runtime_access({"created_by": "alice"}, _user())

    assert exc_info.value.status_code == HTTP_FORBIDDEN


def test_runtime_owner_uses_immutable_user_id_instead_of_username() -> None:
    env = {"scope": "private", "created_by": "alice", "owner_user_id": str(RENAMED_OWNER_ID)}

    ensure_runtime_access(env, _user("alice-renamed", user_id=RENAMED_OWNER_ID))
    with pytest.raises(HTTPException) as exc_info:
        ensure_runtime_access(env, _user("alice", user_id=REUSED_USERNAME_USER_ID))

    assert exc_info.value.status_code == HTTP_FORBIDDEN


def test_legacy_private_runtime_without_owner_id_fails_closed() -> None:
    with pytest.raises(HTTPException) as exc_info:
        ensure_runtime_access(
            {"scope": "private", "created_by": "alice"},
            _user("alice", user_id=RENAMED_OWNER_ID),
        )

    assert exc_info.value.status_code == HTTP_FORBIDDEN
