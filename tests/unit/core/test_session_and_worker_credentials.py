from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from antcode_core.application.services.workers.worker_service import WorkerService
from antcode_core.common.config import settings
from antcode_core.common.security.api_key import (
    hash_api_key,
    store_api_key,
    store_secret_key,
)
from antcode_core.common.security.auth import get_current_user, jwt_auth, jwt_secret_manager
from antcode_core.common.security.secret_box import secret_box
from antcode_core.domain.models.user import User, UserRole
from antcode_core.domain.models.user_session import UserSession
from antcode_core.domain.models.worker import Worker
from antcode_core.domain.schemas.project import ExtractionRule
from pydantic import ValidationError


@pytest.fixture(autouse=True)
def auth_secrets(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "session-tests-jwt-secret-0123456789")
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", "session-tests-encryption-key-32-bytes")
    monkeypatch.setattr(settings, "ENCRYPTION_KEY_SALT", "session-tests-salt-value")
    monkeypatch.setattr(settings, "ENCRYPTION_LEGACY_KDF_SALT", "")
    monkeypatch.setattr(settings, "ENCRYPTION_ALLOW_LEGACY_SHA256", False)
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
            return SimpleNamespace(user_id=7, revoked_at=None)

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
