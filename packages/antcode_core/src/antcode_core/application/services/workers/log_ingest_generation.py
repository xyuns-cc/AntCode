"""Lease-generation cutoffs for already accepted log-ingest messages."""

from __future__ import annotations

from typing import Any

from antcode_core.common.redis_stream_id import (
    MAX_STREAM_ID_LENGTH,
    parse_stream_id,
    stream_id_not_after,
)
from antcode_core.infrastructure.redis.control_plane import log_ingest_stream_key

EMPTY_STREAM_ID = "0-0"


async def read_log_ingest_cutoff(redis: Any, *, namespace: str | None = None) -> str:
    stream_key = log_ingest_stream_key(namespace)
    if not await redis.exists(stream_key):
        return EMPTY_STREAM_ID
    info = await redis.xinfo_stream(stream_key)
    raw = info.get("last-generated-id", info.get(b"last-generated-id"))
    if isinstance(raw, bytes):
        raw = raw.decode("ascii", errors="strict")
    cutoff = str(raw or "")
    parse_stream_id(cutoff)
    return cutoff


__all__ = [
    "EMPTY_STREAM_ID",
    "MAX_STREAM_ID_LENGTH",
    "parse_stream_id",
    "read_log_ingest_cutoff",
    "stream_id_not_after",
]
