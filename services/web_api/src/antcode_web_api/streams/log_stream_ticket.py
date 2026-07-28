"""Namespaced, one-time claims used to authenticate an SSE log stream."""

from __future__ import annotations

import json
import secrets
from dataclasses import asdict, dataclass
from typing import Any

from antcode_core.infrastructure.redis import redis_namespace

STREAM_TICKET_TTL_SECONDS = 60
TICKET_CREATE_ATTEMPTS = 2


class InvalidStreamTicketError(ValueError):
    """The stored ticket payload is missing or malformed."""


@dataclass(frozen=True)
class StreamTicketClaims:
    user_id: int
    session_jti: str
    run_id: str


def stream_ticket_key(ticket: str, *, namespace: str | None = None) -> str:
    return f"{redis_namespace(namespace)}:sse:ticket:{ticket}"


async def create_stream_ticket(redis: Any, claims: StreamTicketClaims) -> str:
    payload = json.dumps(asdict(claims), ensure_ascii=True, separators=(",", ":"))
    for _ in range(TICKET_CREATE_ATTEMPTS):
        ticket = secrets.token_urlsafe(32)
        stored = await redis.set(
            stream_ticket_key(ticket),
            payload,
            nx=True,
            ex=STREAM_TICKET_TTL_SECONDS,
        )
        if stored:
            return ticket
    raise RuntimeError("无法生成唯一的 SSE 一次性票据")


async def consume_stream_ticket(redis: Any, ticket: str) -> StreamTicketClaims:
    raw = await redis.getdel(stream_ticket_key(ticket))
    if not raw:
        raise InvalidStreamTicketError("票据无效或已过期")
    text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
    return decode_stream_ticket_claims(text)


def decode_stream_ticket_claims(value: str) -> StreamTicketClaims:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise InvalidStreamTicketError("票据声明无效") from exc
    if not isinstance(payload, dict):
        raise InvalidStreamTicketError("票据声明无效")
    user_id = payload.get("user_id")
    session_jti = payload.get("session_jti")
    run_id = payload.get("run_id")
    if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
        raise InvalidStreamTicketError("票据声明无效")
    if not isinstance(session_jti, str) or not session_jti:
        raise InvalidStreamTicketError("票据声明无效")
    if not isinstance(run_id, str) or not run_id:
        raise InvalidStreamTicketError("票据声明无效")
    return StreamTicketClaims(user_id=user_id, session_jti=session_jti, run_id=run_id)


__all__ = [
    "InvalidStreamTicketError",
    "STREAM_TICKET_TTL_SECONDS",
    "StreamTicketClaims",
    "consume_stream_ticket",
    "create_stream_ticket",
    "decode_stream_ticket_claims",
    "stream_ticket_key",
]
