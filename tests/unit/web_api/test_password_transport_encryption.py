"""改密与重置密码必须和登录同一套口令传输策略。

浏览器走查实测（2026-08-20）：``PUT /users/{id}/password`` 把 ``old_password`` /
``new_password`` 以明文 JSON 发送，而 ``/auth/login`` 走 ``/auth/public-key`` 的
RSA-OAEP-256 公钥加密。同一类机密两个端点两种待遇——git 史证实这不是权衡后的
决定，两条路由是同一个提交里写下的，改密只是没跟上。
"""

from __future__ import annotations

import base64
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
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from fastapi import HTTPException, status

OPERATOR = SimpleNamespace(user_id=7, username="ops")
TARGET = SimpleNamespace(id=7, username="ops")
NEW_PASSWORD = "Strong#12345"
OLD_PASSWORD = "Old#12345"


@pytest.fixture(scope="module")
def login_key(tmp_path_factory):
    """独立密钥，避免污染仓库 data_dir 下的真实登录私钥。"""
    key_dir = tmp_path_factory.mktemp("login-keys")
    crypto = login_crypto.LoginPasswordCrypto()
    crypto._resolve_private_key_path = lambda: key_dir / "private.pem"  # noqa: SLF001
    crypto._resolve_public_key_path = lambda: key_dir / "public.pem"  # noqa: SLF001
    return crypto


@pytest.fixture
def crypto(monkeypatch, login_key):
    monkeypatch.setattr(login_crypto, "login_password_crypto", login_key)
    monkeypatch.setattr(settings, "LOGIN_PASSWORD_ENCRYPTION_ENABLED", True)
    monkeypatch.setattr(settings, "LOGIN_PASSWORD_ENCRYPTION_REQUIRED", True)
    return login_key


def _encrypt(crypto, plaintext: str) -> str:
    public_key = crypto._get_private_key().public_key()  # noqa: SLF001
    cipher = public_key.encrypt(
        plaintext.encode("utf-8"),
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    return base64.b64encode(cipher).decode("ascii")


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
async def test_change_password_rejects_plaintext_by_default(crypto, http_request, captured_service) -> None:
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
async def test_change_password_decrypts_envelope_to_plaintext(crypto, http_request, captured_service) -> None:
    await users_password.change_password(
        UserPasswordUpdateRequest(
            encrypted_old_password=_encrypt(crypto, OLD_PASSWORD),
            encrypted_new_password=_encrypt(crypto, NEW_PASSWORD),
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
async def test_own_password_endpoint_decrypts_envelope(crypto, http_request, captured_service) -> None:
    """走查抓到的正是这条路由：前端 authService.changePassword 打的是它。"""
    await users_password.update_user_password(
        "self-public",
        UserPasswordUpdateRequest(
            encrypted_old_password=_encrypt(crypto, OLD_PASSWORD),
            encrypted_new_password=_encrypt(crypto, NEW_PASSWORD),
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
async def test_admin_reset_password_decrypts_envelope(crypto, http_request, captured_service) -> None:
    await users_password.reset_user_password(
        "target-public",
        UserAdminPasswordUpdateRequest(
            encrypted_new_password=_encrypt(crypto, NEW_PASSWORD),
            encryption=login_crypto.LOGIN_ENCRYPTION_ALGORITHM,
            key_id=crypto.public_key_payload()["key_id"],
        ),
        OPERATOR,
        http_request=http_request,
    )

    assert captured_service.reset.await_args.args[1] == NEW_PASSWORD


@pytest.mark.asyncio
async def test_admin_reset_password_rejects_plaintext_by_default(crypto, http_request, captured_service) -> None:
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
async def test_stale_key_id_is_rejected_instead_of_silently_failing(crypto, http_request, captured_service) -> None:
    """密钥轮换后前端拿着旧公钥来改密，必须明确报"刷新页面"，不能糊成密码错误。"""
    with pytest.raises(HTTPException) as exc_info:
        await users_password.change_password(
            UserPasswordUpdateRequest(
                encrypted_old_password=_encrypt(crypto, OLD_PASSWORD),
                encrypted_new_password=_encrypt(crypto, NEW_PASSWORD),
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
