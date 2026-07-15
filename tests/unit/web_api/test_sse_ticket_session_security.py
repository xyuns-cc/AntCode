"""SSE stream tickets must remain bound to a live server-side session (P1-09)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.domain.models import UserSession
from antcode_web_api.routes.v1 import log_stream
from fastapi import HTTPException, status


class _Redis:
    def __init__(self, value: str | None) -> None:
        self._value = value

    async def getdel(self, _key: str):
        return self._value


def _session_query(session):
    return SimpleNamespace(first=AsyncMock(return_value=session))


@pytest.mark.asyncio
async def test_ticket_is_rejected_after_issuing_session_is_revoked(monkeypatch) -> None:
    redis_module = __import__("antcode_core.infrastructure.redis", fromlist=["get_redis_client"])
    monkeypatch.setattr(redis_module, "get_redis_client", AsyncMock(return_value=_Redis("7:session-jti")))
    monkeypatch.setattr(UserSession, "filter", lambda **_filters: _session_query(None))
    monkeypatch.setattr(log_stream.User, "get_or_none", AsyncMock())

    with pytest.raises(HTTPException) as exc_info:
        await log_stream.resolve_stream_ticket("ticket")

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert exc_info.value.detail == "票据关联会话已失效"


@pytest.mark.asyncio
async def test_ticket_without_session_binding_is_rejected(monkeypatch) -> None:
    redis_module = __import__("antcode_core.infrastructure.redis", fromlist=["get_redis_client"])
    monkeypatch.setattr(redis_module, "get_redis_client", AsyncMock(return_value=_Redis("7")))

    with pytest.raises(HTTPException, match="票据关联会话已失效"):
        await log_stream.resolve_stream_ticket("legacy-ticket")


@pytest.mark.asyncio
async def test_missing_ticket_is_rejected() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await log_stream.resolve_stream_ticket(None)

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_ticket_with_active_session_resolves_bound_identity(monkeypatch) -> None:
    redis_module = __import__("antcode_core.infrastructure.redis", fromlist=["get_redis_client"])
    monkeypatch.setattr(redis_module, "get_redis_client", AsyncMock(return_value=_Redis("7:session-jti")))
    monkeypatch.setattr(UserSession, "filter", lambda **_filters: _session_query(SimpleNamespace()))
    user = SimpleNamespace(id=7, username="alice", is_active=True, is_admin=False, role="user")
    monkeypatch.setattr(log_stream.User, "get_or_none", AsyncMock(return_value=user))

    resolved_user, session_jti = await log_stream.resolve_stream_ticket("ticket")

    assert resolved_user is user
    assert session_jti == "session-jti"


def test_ticket_resolution_does_not_mint_intermediate_jwt() -> None:
    """SSE 校验在同一 HTTP 请求内完成，不再像 WS 那样换发短时 JWT。"""
    source = log_stream.__loader__.get_source(log_stream.__name__)

    assert "create_access_token" not in source
    assert "jwt_auth" not in source
