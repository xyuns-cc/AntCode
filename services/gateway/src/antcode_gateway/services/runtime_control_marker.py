"""Durable evidence for recovering committed runtime-control results."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from antcode_core.common.error_messages import normalize_persisted_error_message

MAX_CONTROL_ERROR_BYTES = 16 * 1024


@dataclass(frozen=True)
class SettlementEvidence:
    event_id: str
    worker_id: str
    request_id: str
    lease_id: str
    success: bool
    data_json: str
    error: str
    fingerprint: str

    def encode(self) -> str:
        return json.dumps(
            {
                "data_json": self.data_json,
                "error": self.error,
                "event_id": self.event_id,
                "fingerprint": self.fingerprint,
                "lease_id": self.lease_id,
                "request_id": self.request_id,
                "success": self.success,
                "version": 1,
                "worker_id": self.worker_id,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    def reply_payload(self) -> dict[str, str]:
        return {
            "request_id": self.request_id,
            "success": str(self.success).lower(),
            "data": self.data_json,
            "error": self.error,
            "settlement": self.fingerprint,
        }


def build_settlement_evidence(
    *,
    event_id: str,
    worker_id: str,
    request: Any,
    fingerprint: str,
    canonical_data_json: str,
) -> SettlementEvidence:
    return SettlementEvidence(
        event_id=event_id,
        worker_id=worker_id,
        request_id=request.request_id,
        lease_id=request.lease_id,
        success=bool(request.success),
        data_json=canonical_data_json,
        error=normalize_persisted_error_message(request.error, max_bytes=MAX_CONTROL_ERROR_BYTES) or "",
        fingerprint=fingerprint,
    )


def decode_settlement_evidence(value: Any) -> SettlementEvidence | None:
    text = _text(value)
    if not text.startswith("{"):
        return None
    payload = json.loads(text, parse_constant=_reject_constant)
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("运行时 settlement marker 版本无效")
    success = payload.get("success")
    if not isinstance(success, bool):
        raise ValueError("运行时 settlement marker success 无效")
    fields = {
        key: _required_text(payload, key)
        for key in ("event_id", "worker_id", "request_id", "lease_id", "data_json", "fingerprint")
    }
    error = payload.get("error", "")
    if not isinstance(error, str):
        raise ValueError("运行时 settlement marker error 无效")
    return SettlementEvidence(success=success, error=error, **fields)


def require_matching_evidence(
    evidence: SettlementEvidence,
    *,
    event_id: str,
    worker_id: str,
    request_id: str,
) -> None:
    expected = (event_id, worker_id, request_id)
    actual = (evidence.event_id, evidence.worker_id, evidence.request_id)
    if actual != expected:
        raise ValueError("运行时 settlement marker 身份不匹配")
    if evidence.data_json != canonical_data_json(evidence.data_json):
        raise ValueError("运行时 settlement marker data_json 非规范格式")
    if evidence.fingerprint != _evidence_fingerprint(evidence):
        raise ValueError("运行时 settlement marker fingerprint 无效")


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"运行时 settlement marker {key} 无效")
    return value


def _reject_constant(value: str) -> None:
    raise ValueError(f"运行时 settlement marker 包含非标准 JSON 常量: {value}")


def canonical_data_json(value: str) -> str:
    if not value:
        raise ValueError("运行时结果 data_json 不能为空；无数据必须使用 null")
    parsed = json.loads(value, parse_constant=_reject_constant)
    return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _evidence_fingerprint(evidence: SettlementEvidence) -> str:
    canonical = json.dumps(
        {
            "data": json.loads(evidence.data_json),
            "error": evidence.error,
            "event_id": evidence.event_id,
            "lease_id": evidence.lease_id,
            "request_id": evidence.request_id,
            "success": evidence.success,
            "worker_id": evidence.worker_id,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="strict")
    return str(value)
