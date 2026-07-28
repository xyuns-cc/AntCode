"""Stable PostgreSQL cursors plus legacy Redis ingest event identifiers."""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_INGEST_CURSOR_LENGTH = 96
MAX_POSTGRES_STORAGE_ID = 9_223_372_036_854_775_807
_REDIS_MESSAGE_ID_PATTERN = re.compile(r"(0|[1-9][0-9]*)-(0|[1-9][0-9]*)\Z", re.ASCII)
_INGEST_EVENT_ID_PATTERN = re.compile(
    r"(0|[1-9][0-9]*)-(0|[1-9][0-9]*):(0|[1-9][0-9]*)\Z",
    re.ASCII,
)
_POSTGRES_CURSOR_PATTERN = re.compile(r"pg:([1-9][0-9]*)\Z", re.ASCII)


@dataclass(frozen=True)
class PostgresLogCursor:
    """A committed ``task_logs.id`` used by the current SSE protocol."""

    storage_id: int

    @property
    def event_id(self) -> str:
        return format_postgres_cursor(self.storage_id)


@dataclass(frozen=True)
class IngestCursor:
    """A legacy Redis message ID plus its zero-based LogBatch entry index."""

    message_id: str
    batch_index: int

    @property
    def event_id(self) -> str:
        return f"{self.message_id}:{self.batch_index}"


def parse_ingest_cursor(value: str) -> IngestCursor:
    """Parse the canonical ``Redis-ID:batch-index`` resume cursor."""
    if not value or len(value) > MAX_INGEST_CURSOR_LENGTH:
        raise ValueError("cursor 格式无效")
    match = _INGEST_EVENT_ID_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError("cursor 必须为 Redis消息ID:批内下标")
    message_id = f"{match.group(1)}-{match.group(2)}"
    return IngestCursor(message_id=message_id, batch_index=int(match.group(3)))


LogStreamCursor = PostgresLogCursor | IngestCursor


def parse_log_stream_cursor(value: str) -> LogStreamCursor:
    """Parse the current PG cursor or a legacy Redis ingest cursor."""
    if not value or len(value) > MAX_INGEST_CURSOR_LENGTH:
        raise ValueError("cursor 格式无效")
    match = _POSTGRES_CURSOR_PATTERN.fullmatch(value)
    if match is not None:
        storage_id = int(match.group(1))
        if storage_id > MAX_POSTGRES_STORAGE_ID:
            raise ValueError("PG 日志游标超出 BIGINT 范围")
        return PostgresLogCursor(storage_id=storage_id)
    return parse_ingest_cursor(value)


def format_postgres_cursor(storage_id: int) -> str:
    invalid = isinstance(storage_id, bool) or not isinstance(storage_id, int)
    if invalid or storage_id <= 0 or storage_id > MAX_POSTGRES_STORAGE_ID:
        raise ValueError("PG 日志游标必须为正整数")
    return f"pg:{storage_id}"


def stable_ingest_event_id(value: object) -> str | None:
    """Return a canonical direct-ingest ID, excluding legacy arbitrary IDs."""
    if not isinstance(value, str):
        return None
    try:
        return parse_ingest_cursor(value).event_id
    except ValueError:
        return None


def redis_message_id_key(value: str) -> tuple[int, int]:
    """Return the numeric ordering key for a canonical Redis Stream ID."""
    if len(value) > MAX_INGEST_CURSOR_LENGTH:
        raise ValueError("Redis 消息 ID 格式无效")
    match = _REDIS_MESSAGE_ID_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError("Redis 消息 ID 格式无效")
    return int(match.group(1)), int(match.group(2))


__all__ = [
    "IngestCursor",
    "LogStreamCursor",
    "MAX_INGEST_CURSOR_LENGTH",
    "MAX_POSTGRES_STORAGE_ID",
    "PostgresLogCursor",
    "format_postgres_cursor",
    "parse_ingest_cursor",
    "parse_log_stream_cursor",
    "redis_message_id_key",
    "stable_ingest_event_id",
]
