from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.workers.worker_service import WorkerService
from antcode_core.common.config import settings
from antcode_core.common.security.api_key import (
    hash_api_key,
    store_api_key,
    store_secret_key,
)
from antcode_core.common.security.auth import (
    get_current_user,
    get_optional_current_user,
    jwt_auth,
    jwt_secret_manager,
    optional_security,
    rotate_refresh_session,
    verify_refresh_token,
)
from antcode_core.common.security.secret_box import secret_box
from antcode_core.common.security.session_expiry import session_is_unexpired
from antcode_core.domain.models.user import User, UserRole
from antcode_core.domain.models.user_session import UserSession
from antcode_core.domain.models.worker import Worker
from antcode_core.domain.schemas.project import ExtractionRule
from fastapi import HTTPException
from pydantic import ValidationError


@pytest.fixture(autouse=True)
def auth_secrets(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "session-tests-jwt-secret-0123456789")
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", "session-tests-encryption-key-32-bytes")
    monkeypatch.setattr(settings, "ENCRYPTION_KEY_SALT", "session-tests-salt-value")
    monkeypatch.setattr(settings, "ENCRYPTION_LEGACY_KDF_SALT", "")
    jwt_secret_manager._secret = None
    secret_box._cached = None
    secret_box._cache_key = None


@pytest.mark.asyncio
async def test_access_token_resolves_active_server_session(monkeypatch):
    session_jti = "jti-1"
    token = jwt_auth.create_access_token(
        7,
        "old-name",
        session_jti=session_jti,
    )

    class SessionQuery:
        async def first(self):
            return SimpleNamespace(
                user_id=7,
                revoked_at=None,
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )

    async def get_user(**_kwargs):
        return SimpleNamespace(
            username="current-name",
            is_admin=True,
            is_active=True,
            role=UserRole.ADMIN,
        )

    monkeypatch.setattr(UserSession, "filter", lambda **_kwargs: SessionQuery())
    monkeypatch.setattr(User, "get_or_none", get_user)

    result = await get_current_user(SimpleNamespace(credentials=token))

    assert result.user_id == 7
    assert result.username == "current-name"
    assert result.role == "admin"
    assert result.session_jti == session_jti


@pytest.mark.asyncio
async def test_old_admin_token_resolves_live_demoted_role(monkeypatch):
    session_jti = "jti-demoted-admin"
    token = jwt_auth.create_access_token(
        7,
        "admin-before-demotion",
        is_admin=True,
        role=UserRole.ADMIN.value,
        session_jti=session_jti,
    )

    class SessionQuery:
        async def first(self):
            return SimpleNamespace(
                user_id=7,
                revoked_at=None,
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )

    async def get_user(**_kwargs):
        return SimpleNamespace(
            username="demoted-user",
            is_admin=False,
            is_active=True,
            role=UserRole.USER,
        )

    monkeypatch.setattr(UserSession, "filter", lambda **_kwargs: SessionQuery())
    monkeypatch.setattr(User, "get_or_none", get_user)

    result = await get_current_user(SimpleNamespace(credentials=token))

    assert result.username == "demoted-user"
    assert result.role == UserRole.USER.value
    assert result.is_admin is False
    assert result.session_jti == session_jti


@pytest.mark.asyncio
async def test_access_token_rejects_shortened_server_session(monkeypatch):
    token = jwt_auth.create_access_token(7, "user", session_jti="jti-expired")
    captured: dict = {}

    class SessionQuery:
        async def first(self):
            return SimpleNamespace(expires_at=datetime.now(UTC) - timedelta(seconds=1))

    def filter_session(**filters):
        captured.update(filters)
        return SessionQuery()

    monkeypatch.setattr(UserSession, "filter", filter_session)
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(SimpleNamespace(credentials=token))

    assert exc_info.value.status_code == 401
    assert captured["expires_at__gt"].tzinfo is UTC


@pytest.mark.asyncio
async def test_refresh_rejects_shortened_server_session(monkeypatch):
    token, _jti, _expiry = jwt_auth.create_refresh_token(7, "user")
    captured: dict = {}

    class SessionQuery:
        async def first(self):
            return SimpleNamespace(expires_at=datetime.now(UTC) - timedelta(seconds=1))

    def filter_session(**filters):
        captured.update(filters)
        return SessionQuery()

    monkeypatch.setattr(UserSession, "filter", filter_session)
    with pytest.raises(HTTPException) as exc_info:
        await verify_refresh_token(token)

    assert exc_info.value.status_code == 401
    assert captured["user_id"] == 7
    assert captured["expires_at__gt"].tzinfo is UTC


@pytest.mark.asyncio
async def test_refresh_rotation_cas_requires_unexpired_session(monkeypatch):
    import tortoise.transactions

    captured: dict = {}

    class Transaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return None

    class SessionQuery:
        def using_db(self, _connection):
            return self

        async def update(self, **_values):
            return 0

    def filter_session(**filters):
        captured.update(filters)
        return SessionQuery()

    monkeypatch.setattr(tortoise.transactions, "in_transaction", lambda: Transaction())
    monkeypatch.setattr(UserSession, "filter", filter_session)
    with pytest.raises(HTTPException):
        await rotate_refresh_session(
            user_id=7,
            previous_jti="old",
            new_jti="new",
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )

    assert captured["expires_at__gt"].tzinfo is UTC


def test_session_expiry_interprets_naive_timestamps_as_utc():
    now = datetime.now(UTC)
    future_naive = (now + timedelta(minutes=1)).replace(tzinfo=None)
    past_naive = (now - timedelta(minutes=1)).replace(tzinfo=None)

    assert session_is_unexpired(SimpleNamespace(expires_at=future_naive), now=now)
    assert not session_is_unexpired(SimpleNamespace(expires_at=past_naive), now=now)


def test_worker_credentials_are_hash_or_ciphertext_only():
    worker = SimpleNamespace()
    store_api_key(worker, "api-secret")
    store_secret_key(worker, "hmac-secret")

    assert worker.api_key_hash == hash_api_key("api-secret")
    assert worker.secret_key_hash == hash_api_key("hmac-secret")
    assert secret_box.decrypt(worker.secret_key_encrypted) == "hmac-secret"
    assert not hasattr(worker, "api_key")
    assert not hasattr(worker, "secret_key")
    assert "api_key" not in Worker._meta.fields_map
    assert "secret_key" not in Worker._meta.fields_map


def test_jsonpath_rule_is_rejected_at_schema_boundary():
    with pytest.raises(ValidationError):
        ExtractionRule(desc="title", type="jsonpath", expr="$.title")


def test_refresh_token_contains_unique_jti():
    first = jwt_auth.create_refresh_token(1, "user", timedelta(minutes=5))
    second = jwt_auth.create_refresh_token(1, "user", timedelta(minutes=5))

    assert first[1] != second[1]
    assert first[2] > datetime.now(UTC).replace(tzinfo=None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("expires_at", "expected"),
    [
        (datetime.now(UTC) + timedelta(minutes=5), True),
        (datetime.now(UTC) - timedelta(seconds=1), False),
        (None, False),
    ],
)
async def test_worker_service_previous_api_key_honors_expiry(expires_at, expected):
    worker = SimpleNamespace(
        api_key_hash=hash_api_key("current-key"),
        api_key_previous_hash=hash_api_key("previous-key"),
        api_key_previous_expires_at=expires_at,
    )

    assert await WorkerService().verify_api_key(worker, "previous-key") is expected


@pytest.mark.asyncio
async def test_optional_auth_allows_anonymous_requests():
    assert optional_security.auto_error is False
    assert await get_optional_current_user(None) is None


@pytest.mark.asyncio
async def test_optional_auth_uses_current_session_and_role(monkeypatch):
    import antcode_core.common.security.auth as auth_module

    expected = SimpleNamespace(user_id=7, is_admin=False, role="user")
    verify = AsyncMock(return_value=expected)
    monkeypatch.setattr(auth_module, "get_current_user", verify)
    credentials = SimpleNamespace(credentials="token")

    result = await get_optional_current_user(credentials)

    assert result is expected
    verify.assert_awaited_once_with(credentials)
