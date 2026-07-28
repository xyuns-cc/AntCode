"""One-time SSE ticket storage and claim validation."""

from unittest.mock import AsyncMock

import pytest
from antcode_web_api.streams import log_stream_ticket
from antcode_web_api.streams.log_stream_ticket import (
    InvalidStreamTicketError,
    StreamTicketClaims,
    consume_stream_ticket,
    create_stream_ticket,
    decode_stream_ticket_claims,
    stream_ticket_key,
)


@pytest.mark.asyncio
async def test_ticket_is_namespaced_bound_and_consumed(monkeypatch) -> None:
    redis = AsyncMock()
    redis.set.return_value = True
    redis.getdel.return_value = b'{"user_id":7,"session_jti":"jti","run_id":"run-1"}'
    monkeypatch.setattr(log_stream_ticket.secrets, "token_urlsafe", lambda _size: "ticket")

    claims = StreamTicketClaims(user_id=7, session_jti="jti", run_id="run-1")
    ticket = await create_stream_ticket(redis, claims)
    resolved = await consume_stream_ticket(redis, ticket)

    assert ticket == "ticket"
    assert resolved == claims
    key = stream_ticket_key(ticket)
    assert key.endswith(":sse:ticket:ticket")
    redis.set.assert_awaited_once_with(
        key,
        '{"user_id":7,"session_jti":"jti","run_id":"run-1"}',
        nx=True,
        ex=log_stream_ticket.STREAM_TICKET_TTL_SECONDS,
    )
    redis.getdel.assert_awaited_once_with(key)


@pytest.mark.parametrize(
    "payload",
    [
        "not-json",
        "[]",
        "{}",
        '{"user_id":true,"session_jti":"jti","run_id":"run-1"}',
        '{"user_id":"7","session_jti":"jti","run_id":"run-1"}',
        '{"user_id":0,"session_jti":"jti","run_id":"run-1"}',
        '{"user_id":7,"session_jti":"","run_id":"run-1"}',
        '{"user_id":7,"session_jti":"jti","run_id":""}',
    ],
)
def test_invalid_claims_are_rejected(payload: str) -> None:
    with pytest.raises(InvalidStreamTicketError, match="票据"):
        decode_stream_ticket_claims(payload)


@pytest.mark.asyncio
async def test_ticket_collision_failure_is_explicit(monkeypatch) -> None:
    redis = AsyncMock()
    redis.set.return_value = False
    monkeypatch.setattr(log_stream_ticket.secrets, "token_urlsafe", lambda _size: "collision")

    with pytest.raises(RuntimeError, match="唯一"):
        await create_stream_ticket(redis, StreamTicketClaims(7, "jti", "run-1"))

    assert redis.set.await_count == log_stream_ticket.TICKET_CREATE_ATTEMPTS
