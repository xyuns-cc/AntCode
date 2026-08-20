"""建用户的初始口令必须和登录/改密/重置同一套传输策略。

实测（2026-08-20，mn 栈）：``POST /api/v1/users/`` 带明文 ``password`` 建号
**201 成功**，而同一部署下另外三条口令路由拒收明文——"口令不得明文过线"
只做了四分之三，管理员建号时设的初始口令仍然明文过线。

策略入口沿用 ``resolve_transmitted_password``，开关沿用
``LOGIN_PASSWORD_ENCRYPTION_REQUIRED``，不另起第二套。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.common.config import settings
from antcode_core.common.security import login_crypto
from antcode_core.domain.schemas.user import UserCreateRequest
from antcode_web_api.routes.v1 import users
from fastapi import HTTPException, status

OPERATOR = SimpleNamespace(user_id=7, username="ops")
CREATED = SimpleNamespace(id=11, username="newcomer")
NEW_PASSWORD = "Strong#12345"


@pytest.fixture
def captured_create(monkeypatch):
    """拦下 service 层，断言到达它的初始口令是解密后的明文。

    只有 ``_build_user_response`` 被 mock 掉——它要一个真 ORM 对象，且不在本
    组用例的判据里；口令解析本身走的是真实代码路径。
    """
    create = AsyncMock(return_value=CREATED)
    monkeypatch.setattr(users.user_service, "create_user", create)
    monkeypatch.setattr(users.user_service, "get_user_by_id", AsyncMock(return_value=OPERATOR))
    monkeypatch.setattr(users, "audit_user_created", AsyncMock())
    monkeypatch.setattr(users, "_build_user_response", AsyncMock(return_value=None))
    return create


def _request(**overrides) -> UserCreateRequest:
    return UserCreateRequest(username="newcomer", is_admin=False, **overrides)


@pytest.mark.asyncio
async def test_create_user_rejects_plaintext_initial_password(crypto, http_request, captured_create) -> None:
    """明文建号必须被拒——这正是另外三条路由早就在做的事。"""
    with pytest.raises(HTTPException) as exc_info:
        await users.create_user(_request(password=NEW_PASSWORD), http_request, OPERATOR)

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "密码必须加密传输"
    captured_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_user_decrypts_envelope_to_plaintext(
    crypto, http_request, captured_create, *, encrypt_password
) -> None:
    """密文建号必须把明文交到 service：只断言 201 证明不了口令存对了。"""
    await users.create_user(
        _request(
            encrypted_password=encrypt_password(NEW_PASSWORD),
            encryption=login_crypto.LOGIN_ENCRYPTION_ALGORITHM,
            key_id=crypto.public_key_payload()["key_id"],
        ),
        http_request,
        OPERATOR,
    )

    assert captured_create.await_args.args[0].password == NEW_PASSWORD


@pytest.mark.asyncio
async def test_create_user_rejects_stale_key_id(crypto, http_request, captured_create, *, encrypt_password) -> None:
    """旧公钥建号要明确报「密钥已过期」，不能糊成「密码解密失败」。"""
    with pytest.raises(HTTPException) as exc_info:
        await users.create_user(
            _request(
                encrypted_password=encrypt_password(NEW_PASSWORD),
                encryption=login_crypto.LOGIN_ENCRYPTION_ALGORITHM,
                key_id="stale-key-id",
            ),
            http_request,
            OPERATOR,
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == login_crypto.STALE_LOGIN_KEY_MESSAGE
    captured_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_user_plaintext_accepted_when_operator_opts_out(
    crypto, http_request, captured_create, *, monkeypatch
) -> None:
    """建号与另外三条共用同一个开关，不另起一套。"""
    monkeypatch.setattr(settings, "LOGIN_PASSWORD_ENCRYPTION_REQUIRED", False)

    await users.create_user(_request(password=NEW_PASSWORD), http_request, OPERATOR)

    assert captured_create.await_args.args[0].password == NEW_PASSWORD
