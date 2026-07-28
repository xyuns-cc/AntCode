"""SSE stream tickets must remain bound to a live server-side session (P1-09)."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.domain.models import UserSession
from antcode_web_api.routes.v1 import log_stream
from antcode_web_api.streams import log_stream_access
from fastapi import HTTPException, status


class _Redis:
    def __init__(self, value: str | None) -> None:
        self._value = value

    async def getdel(self, _key: str):
        return self._value


def _session_query(session):
    return SimpleNamespace(first=AsyncMock(return_value=session))


def _ticket_payload(*, session_jti: str = "session-jti", run_id: str = "run-1") -> str:
    return json.dumps({"user_id": 7, "session_jti": session_jti, "run_id": run_id})


@pytest.mark.asyncio
async def test_ticket_is_rejected_after_issuing_session_is_revoked(monkeypatch) -> None:
    redis_module = __import__("antcode_core.infrastructure.redis", fromlist=["get_redis_client"])
    monkeypatch.setattr(redis_module, "get_redis_client", AsyncMock(return_value=_Redis(_ticket_payload())))
    monkeypatch.setattr(UserSession, "filter", lambda **_filters: _session_query(None))
    monkeypatch.setattr(log_stream.User, "get_or_none", AsyncMock())

    with pytest.raises(HTTPException) as exc_info:
        await log_stream.resolve_stream_ticket("ticket", run_id="run-1")

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert exc_info.value.detail == "票据关联会话已失效"


@pytest.mark.asyncio
async def test_ticket_without_session_binding_is_rejected(monkeypatch) -> None:
    redis_module = __import__("antcode_core.infrastructure.redis", fromlist=["get_redis_client"])
    monkeypatch.setattr(
        redis_module, "get_redis_client", AsyncMock(return_value=_Redis(_ticket_payload(session_jti="")))
    )

    with pytest.raises(HTTPException, match="票据声明无效"):
        await log_stream.resolve_stream_ticket("legacy-ticket", run_id="run-1")


@pytest.mark.asyncio
async def test_missing_ticket_is_rejected() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await log_stream.resolve_stream_ticket(None, run_id="run-1")

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_ticket_with_active_session_resolves_bound_identity(monkeypatch) -> None:
    redis_module = __import__("antcode_core.infrastructure.redis", fromlist=["get_redis_client"])
    monkeypatch.setattr(redis_module, "get_redis_client", AsyncMock(return_value=_Redis(_ticket_payload())))
    session = SimpleNamespace(expires_at=datetime.now(UTC) + timedelta(hours=1))
    filters = {}

    def filter_session(**query_filters):
        filters.update(query_filters)
        return _session_query(session)

    monkeypatch.setattr(UserSession, "filter", filter_session)
    user = SimpleNamespace(id=7, username="alice", is_active=True, is_admin=False, role="user")
    monkeypatch.setattr(log_stream.User, "get_or_none", AsyncMock(return_value=user))

    resolved_user, session_jti = await log_stream.resolve_stream_ticket("ticket", run_id="run-1")

    assert resolved_user is user
    assert session_jti == "session-jti"
    assert filters["expires_at__gt"].tzinfo is UTC


@pytest.mark.asyncio
async def test_ticket_cannot_be_reused_for_another_run(monkeypatch) -> None:
    redis_module = __import__("antcode_core.infrastructure.redis", fromlist=["get_redis_client"])
    redis = _Redis(_ticket_payload(run_id="run-other"))
    monkeypatch.setattr(redis_module, "get_redis_client", AsyncMock(return_value=redis))
    load_user = AsyncMock()
    monkeypatch.setattr(log_stream.User, "get_or_none", load_user)

    with pytest.raises(HTTPException) as exc_info:
        await log_stream.resolve_stream_ticket("ticket", run_id="run-1")

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert exc_info.value.detail == "票据未绑定当前执行记录"
    load_user.assert_not_awaited()


@pytest.mark.asyncio
async def test_ticket_is_rejected_when_bound_session_has_expired(monkeypatch) -> None:
    redis_module = __import__("antcode_core.infrastructure.redis", fromlist=["get_redis_client"])
    monkeypatch.setattr(redis_module, "get_redis_client", AsyncMock(return_value=_Redis(_ticket_payload())))
    expired = SimpleNamespace(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    monkeypatch.setattr(UserSession, "filter", lambda **_filters: _session_query(expired))
    monkeypatch.setattr(
        log_stream.User,
        "get_or_none",
        AsyncMock(return_value=SimpleNamespace(is_active=True)),
    )

    with pytest.raises(HTTPException) as exc_info:
        await log_stream.resolve_stream_ticket("ticket", run_id="run-1")

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert exc_info.value.detail == "票据关联会话已失效"


@pytest.mark.asyncio
async def test_periodic_recheck_rejects_expired_session_before_loading_user(monkeypatch) -> None:
    expired = SimpleNamespace(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    monkeypatch.setattr(UserSession, "filter", lambda **_filters: _session_query(expired))
    load_user = AsyncMock()
    monkeypatch.setattr(log_stream_access.User, "get_or_none", load_user)

    valid = await log_stream_access.session_still_valid(7, "session-jti")

    assert valid is False
    load_user.assert_not_awaited()


def test_session_expiry_treats_naive_database_value_as_utc() -> None:
    session = SimpleNamespace(expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=1))

    assert log_stream_access.session_not_expired(session) is True


def test_ticket_resolution_does_not_mint_intermediate_jwt() -> None:
    """SSE 校验在同一 HTTP 请求内完成，不再像 WS 那样换发短时 JWT。"""
    source = log_stream.__loader__.get_source(log_stream.__name__)

    assert "create_access_token" not in source
    assert "jwt_auth" not in source
