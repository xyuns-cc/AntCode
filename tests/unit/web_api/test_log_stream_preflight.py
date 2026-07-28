"""SSE ticket preflight exposes run access and capacity failures over JSON HTTP."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_web_api.routes.v1 import log_stream
from antcode_web_api.streams.log_stream_ticket import StreamTicketClaims
from fastapi import HTTPException


def _token() -> SimpleNamespace:
    return SimpleNamespace(user_id=7, session_jti="session-jti")


@pytest.mark.asyncio
async def test_issue_ticket_preflights_run_and_binds_claims(monkeypatch) -> None:
    user = SimpleNamespace(id=7, is_active=True, is_admin=False)
    execution = SimpleNamespace(run_id="run-1")
    load_user = AsyncMock(return_value=user)
    verify_access = AsyncMock(return_value=execution)
    ensure_capacity = AsyncMock()
    create_ticket = AsyncMock(return_value="ticket")
    redis_module = __import__("antcode_core.infrastructure.redis", fromlist=["get_redis_client"])
    redis = object()
    monkeypatch.setattr(log_stream.User, "get_or_none", load_user)
    monkeypatch.setattr(log_stream, "verify_execution_access", verify_access)
    monkeypatch.setattr(log_stream.run_stream_broker, "ensure_capacity", ensure_capacity)
    monkeypatch.setattr(log_stream, "create_stream_ticket", create_ticket)
    monkeypatch.setattr(redis_module, "get_redis_client", AsyncMock(return_value=redis))

    response = await log_stream.issue_stream_ticket(run_id="run-1", current_user=_token())

    assert response.data == {"ticket": "ticket", "ttl": 60}
    verify_access.assert_awaited_once_with("run-1", user)
    ensure_capacity.assert_awaited_once_with("run-1", 7)
    create_ticket.assert_awaited_once_with(
        redis,
        StreamTicketClaims(user_id=7, session_jti="session-jti", run_id="run-1"),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [403, 404])
async def test_issue_ticket_surfaces_run_access_failure(monkeypatch, status_code: int) -> None:
    user = SimpleNamespace(id=7, is_active=True, is_admin=False)
    monkeypatch.setattr(log_stream.User, "get_or_none", AsyncMock(return_value=user))
    monkeypatch.setattr(
        log_stream,
        "verify_execution_access",
        AsyncMock(side_effect=HTTPException(status_code=status_code, detail="denied")),
    )
    create_ticket = AsyncMock()
    monkeypatch.setattr(log_stream, "create_stream_ticket", create_ticket)

    with pytest.raises(HTTPException) as exc_info:
        await log_stream.issue_stream_ticket(run_id="run-1", current_user=_token())

    assert exc_info.value.status_code == status_code
    create_ticket.assert_not_awaited()


@pytest.mark.asyncio
async def test_issue_ticket_surfaces_capacity_failure(monkeypatch) -> None:
    user = SimpleNamespace(id=7, is_active=True, is_admin=False)
    monkeypatch.setattr(log_stream.User, "get_or_none", AsyncMock(return_value=user))
    monkeypatch.setattr(log_stream, "verify_execution_access", AsyncMock(return_value=SimpleNamespace()))
    monkeypatch.setattr(
        log_stream.run_stream_broker,
        "ensure_capacity",
        AsyncMock(side_effect=log_stream.StreamLimitExceededError("容量已满")),
    )

    with pytest.raises(HTTPException) as exc_info:
        await log_stream.issue_stream_ticket(run_id="run-1", current_user=_token())

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == "容量已满"
