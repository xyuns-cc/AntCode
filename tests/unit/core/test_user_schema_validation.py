"""R9.1: UserCreate/UserUpdate/UserRoleUpdate 防越权与一致性校验。"""

from __future__ import annotations

import pytest
from antcode_core.domain.schemas.user import (
    AdminUserRoleUpdateRequest,
    UserCreateRequest,
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


def test_user_create_is_admin_without_role_rejected_instead_of_silently_demoted():
    """``is_admin=true`` 不带 ``role`` 是欠定，必须 422，不能回 201「创建成功」。

    以前这里直接放行：``role`` 落库取默认 ``user``，``User._sync_admin_flag`` 再把
    ``is_admin`` 翻回 False——管理员建管理员账号静默失败，提示却是"创建成功"。
    """
    with pytest.raises(ValidationError, match="必须显式提供 role"):
        UserCreateRequest(
            username="alice",
            password="passw0rd!",
            is_admin=True,
        )


@pytest.mark.parametrize("role", ["admin", "super_admin"])
def test_user_create_is_admin_with_explicit_role_still_passes(role):
    """成功臂：一个布尔位分不出 admin / super_admin，说清楚了就必须放行。"""
    req = UserCreateRequest(
        username="alice",
        password="passw0rd!",
        is_admin=True,
        role=role,
    )
    assert (req.is_admin, req.role) == (True, role)


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
    req = AdminUserRoleUpdateRequest(old_role="admin", new_role="super_admin")
    assert req.old_role == "admin"
    assert req.new_role == "super_admin"
    with pytest.raises(ValidationError):
        AdminUserRoleUpdateRequest(old_role="admin", new_role="invalid")
