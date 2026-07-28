"""Durable dead-letter isolation for malformed SSE ingest frames."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from itertools import islice
from typing import Any

from antcode_core.infrastructure.redis import redis_namespace

DLQ_MAXLEN = 10_000
DLQ_MAX_FIELDS = 16
DLQ_MAX_FIELD_BYTES = 2_048
DLQ_MAX_ERROR_CHARS = 2_000


def ingest_dead_letter_key(namespace: str | None = None) -> str:
    return f"{redis_namespace(namespace)}:dead_letter:sse_ingest"


async def isolate_bad_ingest_frame(
    redis: Any,
    *,
    namespace: str,
    source_stream: str,
    message_id: str,
    fields: dict[Any, Any],
    error: Exception,
) -> None:
    """Persist a bounded forensic record; failure must propagate to the caller."""
    entry = {
        "source_stream": source_stream,
        "source_message_id": message_id,
        "decode_error": str(error)[:DLQ_MAX_ERROR_CHARS],
        "isolated_at": datetime.now(UTC).isoformat(),
        "raw_fields": json.dumps(_bounded_fields(fields), ensure_ascii=True, separators=(",", ":")),
    }
    await redis.xadd(
        ingest_dead_letter_key(namespace),
        entry,
        maxlen=DLQ_MAXLEN,
        approximate=True,
    )


def _bounded_fields(fields: dict[Any, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for key, value in islice(fields.items(), DLQ_MAX_FIELDS):
        raw = value if isinstance(value, bytes) else str(value).encode()
        result.append(
            {
                "key": _field_key(key),
                "value_base64": base64.b64encode(raw[:DLQ_MAX_FIELD_BYTES]).decode("ascii"),
                "truncated": len(raw) > DLQ_MAX_FIELD_BYTES,
            }
        )
    return result


def _field_key(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")[:DLQ_MAX_FIELD_BYTES]
    return str(value)[:DLQ_MAX_FIELD_BYTES]


__all__ = ["DLQ_MAXLEN", "ingest_dead_letter_key", "isolate_bad_ingest_frame"]
