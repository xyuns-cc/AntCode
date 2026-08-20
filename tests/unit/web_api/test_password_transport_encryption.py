"""改密与重置密码必须和登录同一套口令传输策略。

浏览器走查实测（2026-08-20）：``PUT /users/{id}/password`` 把 ``old_password`` /
``new_password`` 以明文 JSON 发送，而 ``/auth/login`` 走 ``/auth/public-key`` 的
RSA-OAEP-256 公钥加密。同一类机密两个端点两种待遇——git 史证实这不是权衡后的
决定，两条路由是同一个提交里写下的，改密只是没跟上。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.common.config import settings
from antcode_core.common.security import login_crypto
from antcode_core.domain.schemas.user import (
    UserAdminPasswordUpdateRequest,
    UserPasswordUpdateRequest,
)
from antcode_web_api.routes.v1 import users_password
from fastapi import HTTPException, status

OPERATOR = SimpleNamespace(user_id=7, username="ops")
TARGET = SimpleNamespace(id=7, username="ops")
NEW_PASSWORD = "Strong#12345"
OLD_PASSWORD = "Old#12345"


@pytest.fixture
def http_request():
    return SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={})


@pytest.fixture
def captured_service(monkeypatch):
    """拦下 service 层，断言到达它的是解密后的明文。"""
    update = AsyncMock(return_value=TARGET)
    reset = AsyncMock(return_value=TARGET)
    monkeypatch.setattr(users_password.user_service, "update_user_password", update)
    monkeypatch.setattr(users_password.user_service, "reset_user_password", reset)
    monkeypatch.setattr(
        users_password.user_service,
        "get_user_by_public_id",
        AsyncMock(return_value=TARGET),
    )
    monkeypatch.setattr(users_password, "audit_password_changed", AsyncMock())
    return SimpleNamespace(update=update, reset=reset)


@pytest.mark.asyncio
async def test_change_password_rejects_plaintext_by_default(
    crypto, http_request, captured_service, *, encrypt_password
) -> None:
    """默认策略下明文改密必须被拒——这正是登录早就在做的事。"""
    with pytest.raises(HTTPException) as exc_info:
        await users_password.change_password(
            UserPasswordUpdateRequest(old_password=OLD_PASSWORD, new_password=NEW_PASSWORD),
            OPERATOR,
            http_request=http_request,
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "密码必须加密传输"
    captured_service.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_change_password_decrypts_envelope_to_plaintext(
    crypto, http_request, captured_service, *, encrypt_password
) -> None:
    await users_password.change_password(
        UserPasswordUpdateRequest(
            encrypted_old_password=encrypt_password(OLD_PASSWORD),
            encrypted_new_password=encrypt_password(NEW_PASSWORD),
            encryption=login_crypto.LOGIN_ENCRYPTION_ALGORITHM,
            key_id=crypto.public_key_payload()["key_id"],
        ),
        OPERATOR,
        http_request=http_request,
    )

    resolved = captured_service.update.await_args.args[1]
    assert resolved.old_password == OLD_PASSWORD
    assert resolved.new_password == NEW_PASSWORD


@pytest.mark.asyncio
async def test_own_password_endpoint_decrypts_envelope(
    crypto, http_request, captured_service, *, encrypt_password
) -> None:
    """走查抓到的正是这条路由：前端 authService.changePassword 打的是它。"""
    await users_password.update_user_password(
        "self-public",
        UserPasswordUpdateRequest(
            encrypted_old_password=encrypt_password(OLD_PASSWORD),
            encrypted_new_password=encrypt_password(NEW_PASSWORD),
            encryption=login_crypto.LOGIN_ENCRYPTION_ALGORITHM,
            key_id=crypto.public_key_payload()["key_id"],
        ),
        OPERATOR,
        http_request=http_request,
    )

    resolved = captured_service.update.await_args.args[1]
    assert resolved.old_password == OLD_PASSWORD
    assert resolved.new_password == NEW_PASSWORD


@pytest.mark.asyncio
async def test_admin_reset_password_decrypts_envelope(
    crypto, http_request, captured_service, *, encrypt_password
) -> None:
    await users_password.reset_user_password(
        "target-public",
        UserAdminPasswordUpdateRequest(
            encrypted_new_password=encrypt_password(NEW_PASSWORD),
            encryption=login_crypto.LOGIN_ENCRYPTION_ALGORITHM,
            key_id=crypto.public_key_payload()["key_id"],
        ),
        OPERATOR,
        http_request=http_request,
    )

    assert captured_service.reset.await_args.args[1] == NEW_PASSWORD


@pytest.mark.asyncio
async def test_admin_reset_password_rejects_plaintext_by_default(
    crypto, http_request, captured_service, *, encrypt_password
) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await users_password.reset_user_password(
            "target-public",
            UserAdminPasswordUpdateRequest(new_password=NEW_PASSWORD),
            OPERATOR,
            http_request=http_request,
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    captured_service.reset.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_key_id_is_rejected_instead_of_silently_failing(
    crypto, http_request, captured_service, *, encrypt_password
) -> None:
    """密钥轮换后前端拿着旧公钥来改密，必须明确报"刷新页面"，不能糊成密码错误。"""
    with pytest.raises(HTTPException) as exc_info:
        await users_password.change_password(
            UserPasswordUpdateRequest(
                encrypted_old_password=encrypt_password(OLD_PASSWORD),
                encrypted_new_password=encrypt_password(NEW_PASSWORD),
                encryption=login_crypto.LOGIN_ENCRYPTION_ALGORITHM,
                key_id="stale-key-id",
            ),
            OPERATOR,
            http_request=http_request,
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert "密钥已过期" in exc_info.value.detail
    captured_service.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_key_message_is_the_cross_language_contract(
    crypto, http_request, captured_service, *, encrypt_password
) -> None:
    """这段文案是跨端契约：前端靠它识别"该丢掉缓存的公钥了"。

    utils/loginEncryption.ts 的 STALE_KEY_MARKER 匹配 "登录密钥已过期"；这里逐字
    钉住，改文案而不改前端就会红，而不是等到用户在设置页反复撞 400 才发现。
    另外它不能再说"请刷新登录页面"——改密与建号发生在设置页/用户管理页。
    """
    with pytest.raises(HTTPException) as exc_info:
        await users_password.change_password(
            UserPasswordUpdateRequest(
                encrypted_old_password=encrypt_password(OLD_PASSWORD),
                encrypted_new_password=encrypt_password(NEW_PASSWORD),
                encryption=login_crypto.LOGIN_ENCRYPTION_ALGORITHM,
                key_id="stale-key-id",
            ),
            OPERATOR,
            http_request=http_request,
        )

    assert exc_info.value.detail == login_crypto.STALE_LOGIN_KEY_MESSAGE
    assert login_crypto.STALE_LOGIN_KEY_MESSAGE.startswith("登录密钥已过期")
    assert "登录页面" not in login_crypto.STALE_LOGIN_KEY_MESSAGE


@pytest.mark.asyncio
async def test_plaintext_still_accepted_when_operator_opts_out(
    crypto, http_request, captured_service, *, monkeypatch
) -> None:
    """存量非浏览器客户端的退路：与登录共用同一个开关，不另起一套。"""
    monkeypatch.setattr(settings, "LOGIN_PASSWORD_ENCRYPTION_REQUIRED", False)

    await users_password.change_password(
        UserPasswordUpdateRequest(old_password=OLD_PASSWORD, new_password=NEW_PASSWORD),
        OPERATOR,
        http_request=http_request,
    )

    resolved = captured_service.update.await_args.args[1]
    assert resolved.old_password == OLD_PASSWORD
