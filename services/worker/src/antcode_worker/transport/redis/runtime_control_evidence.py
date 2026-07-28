"""Canonical evidence and validation for Direct runtime-control results."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any

from antcode_core.infrastructure.redis import (
    control_reply_stream,
    require_runtime_control_request_id,
    runtime_control_settlement_key,
)

from antcode_worker.transport.redis.runtime_control_models import (
    ControlSource,
    RuntimeControlResult,
)

CONTROL_REPLY_TTL_SECONDS = 120
CONTROL_REPLY_MAXLEN = 1
MAX_CONTROL_RESULT_BYTES = 1024 * 1024
MAX_CONTROL_ERROR_BYTES = 16 * 1024
MAX_RUNTIME_ACTION_SECONDS = 900
SETTLEMENT_RECOVERY_GRACE_SECONDS = 300
SETTLEMENT_EVIDENCE_VERSION = 1
_REPLY_FIELDS = {"request_id", "success", "data", "error", "lease_id", "settlement"}


class SettlementEvidenceError(ValueError):
    """Stored runtime-control evidence is corrupt or contradictory."""


@dataclass(frozen=True)
class CommittedReply:
    fingerprint: str
    reply_stream: str
    payload: dict[str, str]


def canonical_result_data(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, allow_nan=False)


def validate_result(
    result: RuntimeControlResult,
    data_json: str,
    expected_reply_stream: str,
) -> None:
    if not result.request_id:
        raise ValueError("Direct 运行时控制结果缺少 request_id")
    if result.reply_stream != expected_reply_stream:
        raise ValueError("Direct 运行时控制 reply_stream 非法")
    if len(data_json.encode("utf-8")) > MAX_CONTROL_RESULT_BYTES:
        raise ValueError("Direct 运行时控制 data 超过 1 MiB 上限")
    if len((result.error or "").encode("utf-8")) > MAX_CONTROL_ERROR_BYTES:
        raise ValueError("Direct 运行时控制 error 超过 16 KiB 上限")


def validate_source(
    decoded: dict[str, Any],
    result: RuntimeControlResult,
    expected_reply_stream: str,
) -> None:
    if decoded.get("control_type") != "runtime_manage":
        raise ValueError("Direct 原控制事件不是 runtime_manage")
    if decoded.get("request_id") != result.request_id:
        raise ValueError("Direct 运行时控制 request_id 与原事件不匹配")
    if decoded.get("reply_stream") != expected_reply_stream:
        raise ValueError("Direct 原控制事件 reply_stream 非法")


def reply_payload(
    result: RuntimeControlResult,
    data_json: str,
    fingerprint: str,
    *,
    lease_id: str,
) -> dict[str, str]:
    return {
        "request_id": result.request_id,
        "success": str(bool(result.success)).lower(),
        "data": data_json,
        "error": result.error or "",
        "lease_id": lease_id,
        "settlement": fingerprint,
    }


def encode_settlement_evidence(payload: dict[str, str]) -> str:
    if set(payload) != _REPLY_FIELDS or not all(isinstance(value, str) for value in payload.values()):
        raise SettlementEvidenceError("Direct settlement marker reply 字段非法")
    return json.dumps(
        {"reply": payload, "version": SETTLEMENT_EVIDENCE_VERSION},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )


def decode_settlement_evidence(value: Any) -> dict[str, str]:
    try:
        decoded = json.loads(_text(value), parse_constant=_reject_json_constant)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SettlementEvidenceError("Direct settlement marker 不是合法 JSON") from exc
    if not isinstance(decoded, dict) or set(decoded) != {"reply", "version"}:
        raise SettlementEvidenceError("Direct settlement marker 结构非法")
    if decoded["version"] != SETTLEMENT_EVIDENCE_VERSION:
        raise SettlementEvidenceError("Direct settlement marker 版本不受支持")
    payload = decoded["reply"]
    if not isinstance(payload, dict) or set(payload) != _REPLY_FIELDS:
        raise SettlementEvidenceError("Direct settlement marker reply 字段非法")
    if not all(isinstance(key, str) and isinstance(item, str) for key, item in payload.items()):
        raise SettlementEvidenceError("Direct settlement marker reply 值非法")
    return payload


def validate_committed_reply(
    source: ControlSource,
    decoded_source: dict[str, Any],
    stored: dict[str, Any],
    *,
    worker_id: str,
    namespace: str,
) -> CommittedReply:
    request_id, reply_stream = runtime_source_identity(
        decoded_source,
        worker_id,
        namespace=namespace,
    )
    try:
        if not _REPLY_FIELDS <= stored.keys() or _text(stored["request_id"]) != request_id:
            raise SettlementEvidenceError("Direct 运行时控制结果字段不完整或 request_id 冲突")
        success_value = _text(stored["success"])
        if success_value not in {"true", "false"}:
            raise SettlementEvidenceError("Direct 运行时控制结果 success 非法")
        lease_id = _text(stored["lease_id"])
        if not lease_id:
            raise SettlementEvidenceError("Direct 运行时控制结果缺少 producing lease_id")
        result = RuntimeControlResult(
            request_id=request_id,
            reply_stream=reply_stream,
            success=success_value == "true",
            data=stored["data"],
            error=_text(stored["error"]),
        )
        data_json = canonical_result_data(result.data)
        validate_result(result, data_json, reply_stream)
        fingerprint = settlement_fingerprint(
            source=source,
            result=result,
            data_json=data_json,
            worker_id=worker_id,
            lease_id=lease_id,
        )
    except SettlementEvidenceError:
        raise
    except (TypeError, ValueError) as exc:
        raise SettlementEvidenceError("Direct 运行时控制结果证据非法") from exc
    if _text(stored["settlement"]) != fingerprint:
        raise SettlementEvidenceError("Direct 运行时控制 settlement fingerprint 非法")
    payload = reply_payload(result, data_json, fingerprint, lease_id=lease_id)
    return CommittedReply(fingerprint=fingerprint, reply_stream=reply_stream, payload=payload)


def runtime_source_identity(
    decoded: dict[str, Any],
    worker_id: str,
    *,
    namespace: str,
) -> tuple[str, str]:
    if decoded.get("control_type") != "runtime_manage":
        raise ValueError("Direct 原控制事件不是 runtime_manage")
    request_id = _text(decoded.get("request_id") or "")
    require_runtime_control_request_id(request_id, worker_id)
    expected_reply = control_reply_stream(request_id, namespace)
    if _text(decoded.get("reply_stream") or "") != expected_reply:
        raise ValueError("Direct 原控制事件 reply_stream 非法")
    return request_id, expected_reply


def settlement_fingerprint(
    *,
    source: ControlSource,
    result: RuntimeControlResult,
    data_json: str,
    worker_id: str,
    lease_id: str,
) -> str:
    value = json.dumps(
        {
            "data": json.loads(data_json),
            "error": result.error or "",
            "message_id": source.message_id,
            "lease_id": lease_id,
            "request_id": result.request_id,
            "stream": source.channel.stream_key,
            "success": bool(result.success),
            "worker_id": worker_id,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def settlement_key(
    namespace: str,
    source: ControlSource,
    *,
    worker_id: str,
) -> str:
    identity = f"{source.channel.stream_key}|{source.message_id}|{worker_id}"
    return runtime_control_settlement_key(worker_id, identity, namespace)


def settlement_ttl_seconds(source: dict[str, Any], *, now_ms: int) -> int:
    try:
        expires_at_ms = int(source.get("expires_at_ms") or 0)
    except (TypeError, ValueError):
        expires_at_ms = 0
    remaining = math.ceil(max(0, expires_at_ms - now_ms) / 1000)
    recovery_window = max(MAX_RUNTIME_ACTION_SECONDS, remaining, CONTROL_REPLY_TTL_SECONDS)
    return recovery_window + SETTLEMENT_RECOVERY_GRACE_SECONDS


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="strict")
    return str(value)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"非标准 JSON 常量: {value}")
