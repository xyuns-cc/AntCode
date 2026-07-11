"""R9.1: UserCreate/UserUpdate/UserRoleUpdate 防越权与一致性校验。"""

from __future__ import annotations

import pytest
from antcode_core.domain.schemas.user import (
    UserCreateRequest,
    UserRoleUpdateRequest,
    UserUpdateRequest,
)
from pydantic import ValidationError


def test_user_create_role_user_with_is_admin_true_rejected():
    with pytest.raises(ValidationError, match="不一致"):
        UserCreateRequest(
            username="alice",
            password="passw0rd!",
            role="user",
            is_admin=True,
        )


def test_user_create_role_admin_with_is_admin_false_rejected():
    with pytest.raises(ValidationError, match="不一致"):
        UserCreateRequest(
            username="alice",
            password="passw0rd!",
            role="admin",
            is_admin=False,
        )


def test_user_create_consistent_admin_passes():
    req = UserCreateRequest(
        username="alice",
        password="passw0rd!",
        role="admin",
        is_admin=True,
    )
    assert req.role == "admin"
    assert req.is_admin is True


def test_user_create_role_optional_passes():
    req = UserCreateRequest(
        username="alice",
        password="passw0rd!",
        is_admin=False,
    )
    assert req.role is None
    assert req.is_admin is False


def test_user_create_invalid_role_rejected():
    with pytest.raises(ValidationError, match="user / admin / super_admin"):
        UserCreateRequest(
            username="alice",
            password="passw0rd!",
            role="god",
            is_admin=True,
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"role": "admin"},
        {"is_admin": True},
        {"password": "Valid#123"},
        {"new_password": "Valid#123"},
    ],
)
def test_user_update_rejects_privileged_fields(payload):
    with pytest.raises(ValidationError):
        UserUpdateRequest(**payload)


def test_user_update_profile_fields_allowed():
    req = UserUpdateRequest(username="alice", email="alice@example.com")
    assert req.username == "alice"
    assert req.email == "alice@example.com"


def test_user_role_update_request_strict():
    req = UserRoleUpdateRequest(old_role="admin", new_role="super_admin")
    assert req.old_role == "admin"
    assert req.new_role == "super_admin"
    with pytest.raises(ValidationError):
        UserRoleUpdateRequest(old_role="admin", new_role="invalid")
