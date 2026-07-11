"""R7: 统一权限模型测试。

覆盖：
- User._sync_admin_flag 保证 is_admin 由 role 派生
- require_role / require_permission Depends 拒/放行逻辑
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from antcode_core.common.security.permissions import Permission
from antcode_core.domain.models import UserRole
from antcode_core.domain.models.user import User
from antcode_web_api.deps import require_permission, require_role
from fastapi import HTTPException


def _make_user(role: UserRole, is_admin: bool = False) -> User:
    user = User()
    user.role = role
    user.is_admin = is_admin
    user.username = "tester"
    return user


def test_sync_admin_flag_promotes_to_true():
    user = _make_user(UserRole.ADMIN, is_admin=False)
    user._sync_admin_flag()
    assert user.is_admin is True


def test_sync_admin_flag_demotes_to_false():
    user = _make_user(UserRole.USER, is_admin=True)
    user._sync_admin_flag()
    assert user.is_admin is False


def test_sync_admin_flag_super_admin_is_admin():
    user = _make_user(UserRole.SUPER_ADMIN, is_admin=False)
    user._sync_admin_flag()
    assert user.is_admin is True


@pytest.mark.asyncio
async def test_require_role_accepts_matching():
    dep = require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN)
    user = _make_user(UserRole.ADMIN)
    result = await dep(current_user=user)
    assert result is user


@pytest.mark.asyncio
async def test_require_role_rejects_mismatch():
    dep = require_role(UserRole.SUPER_ADMIN)
    user = _make_user(UserRole.ADMIN)
    with pytest.raises(HTTPException) as exc:
        await dep(current_user=user)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_require_permission_super_admin_bypass():
    dep = require_permission(Permission.PROJECT_DELETE)
    user = _make_user(UserRole.SUPER_ADMIN)
    result = await dep(current_user=user)
    assert result is user


@pytest.mark.asyncio
async def test_require_permission_user_missing_perm_rejected():
    dep = require_permission(Permission.USER_ADMIN)
    user = _make_user(UserRole.USER)
    with pytest.raises(HTTPException) as exc:
        await dep(current_user=user)
    assert exc.value.status_code == 403
    assert "user:admin" in str(exc.value.detail)
