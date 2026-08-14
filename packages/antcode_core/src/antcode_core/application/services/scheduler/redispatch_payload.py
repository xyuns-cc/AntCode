"""Decode and quarantine durable redispatch payloads."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from loguru import logger

from antcode_core.common.security.task_payload_envelope import (
    TaskPayloadEnvelopeError,
    open_redispatch_payload,
)

_EXPECTED_DLQ_PIPELINE_RESULTS = 2
_PAYLOAD_FIELDS = {
    "run_id",
    "task_id",
    "project_id",
    "params",
    "environment_vars",
    "runtime_env_name",
    "timeout",
    "project_type",
    "region",
    "require_render",
    "attempts",
    "reason",
    "enqueued_at_ms",
}


async def decode_claimed_payload(
    redis: Any,
    raw_payload: str,
    *,
    processing_key: str,
    dead_letter_key: str,
    dead_letter_maxlen: int,
) -> dict[str, Any] | None:
    """Decrypt one claimed member or quarantine a permanent poison payload."""
    try:
        item = json.loads(raw_payload)
        if not isinstance(item, dict):
            raise TaskPayloadEnvelopeError("redispatch payload must be a JSON object")
        return validate_redispatch_payload(open_redispatch_payload(item))
    except (json.JSONDecodeError, TaskPayloadEnvelopeError) as exc:
        await _dead_letter_claim(
            redis,
            raw_payload,
            processing_key=processing_key,
            dead_letter_key=dead_letter_key,
            dead_letter_maxlen=dead_letter_maxlen,
            error=exc,
        )
        logger.error(
            "redispatch poison payload moved to DLQ: digest={} error={}",
            _digest(raw_payload),
            type(exc).__name__,
        )
        return None


async def _dead_letter_claim(
    redis: Any,
    raw_payload: str,
    *,
    processing_key: str,
    dead_letter_key: str,
    dead_letter_maxlen: int,
    error: Exception,
) -> None:
    pipe = redis.pipeline(transaction=True)
    pipe.xadd(
        dead_letter_key,
        {
            "payload_sha256": _digest(raw_payload),
            "reason": type(error).__name__,
            "dead_letter_at_ms": str(int(time.time() * 1000)),
        },
        maxlen=dead_letter_maxlen,
        approximate=True,
    )
    pipe.hdel(processing_key, raw_payload)
    results = await pipe.execute()
    if len(results) < _EXPECTED_DLQ_PIPELINE_RESULTS or int(results[1] or 0) != 1:
        raise RuntimeError("redispatch DLQ did not clear the processing entry")


def _digest(raw_payload: str) -> str:
    return hashlib.sha256(raw_payload.encode()).hexdigest()


def validate_redispatch_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if set(payload) != _PAYLOAD_FIELDS:
        raise TaskPayloadEnvelopeError("redispatch payload schema is invalid")
    _require_non_empty_text(payload, "run_id")
    _require_non_empty_text(payload, "project_id")
    _require_non_empty_text(payload, "project_type")
    _require_text(payload, "runtime_env_name")
    _require_text(payload, "reason")
    region = payload["region"]
    if region is not None and not isinstance(region, str):
        raise TaskPayloadEnvelopeError("redispatch region must be a string or null")
    if not isinstance(payload["require_render"], bool):
        raise TaskPayloadEnvelopeError("redispatch require_render must be a boolean")
    _require_integer(payload, "timeout", minimum=1)
    _require_integer(payload, "attempts", minimum=0)
    _require_integer(payload, "enqueued_at_ms", minimum=1)
    task_id = payload["task_id"]
    if task_id is not None and (not isinstance(task_id, int) or isinstance(task_id, bool)):
        raise TaskPayloadEnvelopeError("redispatch task_id must be an integer or null")
    return payload


def _require_text(payload: dict[str, Any], field: str) -> None:
    if not isinstance(payload[field], str):
        raise TaskPayloadEnvelopeError(f"redispatch {field} must be a string")


def _require_non_empty_text(payload: dict[str, Any], field: str) -> None:
    _require_text(payload, field)
    if not payload[field].strip():
        raise TaskPayloadEnvelopeError(f"redispatch {field} must not be empty")


def _require_integer(payload: dict[str, Any], field: str, *, minimum: int) -> None:
    value = payload[field]
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise TaskPayloadEnvelopeError(f"redispatch {field} must be an integer >= {minimum}")


__all__ = ["decode_claimed_payload", "validate_redispatch_payload"]
