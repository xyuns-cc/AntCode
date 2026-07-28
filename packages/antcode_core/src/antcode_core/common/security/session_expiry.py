"""UTC-normalized server-side session expiry checks."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def as_utc(value: datetime) -> datetime:
    """Interpret legacy naive database timestamps as UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def session_is_unexpired(session: Any, *, now: datetime | None = None) -> bool:
    """Return whether a persisted session has a valid future expiry."""
    expires_at = getattr(session, "expires_at", None)
    if not isinstance(expires_at, datetime):
        return False
    current = as_utc(now or datetime.now(UTC))
    return as_utc(expires_at) > current


__all__ = ["as_utc", "session_is_unexpired"]
