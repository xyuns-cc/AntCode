"""Authenticated encryption for sensitive task fields persisted in Redis."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Callable
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from antcode_core.common.security.secret_box import secret_box
from antcode_core.common.task_payload_contract import ENVELOPE_FIELD, ENVELOPE_VERSION

_CONTROL_KIND = "control"
_WORKER_KIND = "worker"
_CONTROL_ALGORITHM = "secretbox-fernet"
_WORKER_ALGORITHM = "fernet-hkdf-sha256"
_READY_FIELDS = ("params", "environment")
_REDISPATCH_FIELDS = ("params", "environment_vars")
_PUBLIC_BINDING_FIELD = "public_binding_sha256"
_MIN_WORKER_SECRET_BYTES = 32
_SENSITIVE_FIELD_NAMES = frozenset((*_READY_FIELDS, "environment_vars"))
_READY_SETTLEMENT_FIELDS = frozenset(("requeue_at", "requeue_count", "requeue_reason"))


class TaskPayloadEnvelopeError(ValueError):
    """A persisted task payload violates the encrypted envelope contract."""


class TaskPayloadDecryptionError(TaskPayloadEnvelopeError):
    """A ciphertext cannot be authenticated with the expected key."""


def seal_ready_payload(
    payload: dict[str, Any],
    *,
    worker_id: str,
    worker_secret: str,
) -> dict[str, Any]:
    """Replace ready-stream params/environment with a per-Worker envelope."""
    plaintext, public = _split_sensitive(payload, _READY_FIELDS)
    public = _normalize_ready_public(public)
    plaintext[_PUBLIC_BINDING_FIELD] = _public_binding(_ready_execution_public(public))
    ciphertext = _worker_fernet(worker_id, worker_secret).encrypt(_encode(plaintext)).decode("ascii")
    public[ENVELOPE_FIELD] = _encode_envelope(
        kind=_WORKER_KIND,
        algorithm=_WORKER_ALGORITHM,
        ciphertext=ciphertext,
    )
    return public


def open_ready_payload(
    payload: dict[str, Any],
    *,
    worker_id: str,
    worker_secret: str,
) -> dict[str, Any]:
    """Authenticate and restore a ready-stream payload for one Worker."""
    envelope, public = _take_envelope(payload, _READY_FIELDS)
    public = _ready_execution_public(public)
    ciphertext = _require_envelope(envelope, kind=_WORKER_KIND, algorithm=_WORKER_ALGORITHM)
    plaintext = _decrypt(
        ciphertext,
        decryptor=_worker_fernet(worker_id, worker_secret).decrypt,
    )
    return _restore_sensitive(
        public,
        plaintext,
        _READY_FIELDS,
    )


def seal_redispatch_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Replace redispatch params/environment_vars with a control-plane envelope."""
    plaintext, public = _split_sensitive(payload, _REDISPATCH_FIELDS)
    plaintext[_PUBLIC_BINDING_FIELD] = _public_binding(public)
    ciphertext = secret_box.encrypt(_encode(plaintext).decode("utf-8"))
    public[ENVELOPE_FIELD] = _encode_envelope(
        kind=_CONTROL_KIND,
        algorithm=_CONTROL_ALGORITHM,
        ciphertext=ciphertext,
    )
    return public


def open_redispatch_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Authenticate and restore a redispatch payload inside the control plane."""
    envelope, public = _take_envelope(payload, _REDISPATCH_FIELDS)
    ciphertext = _require_envelope(envelope, kind=_CONTROL_KIND, algorithm=_CONTROL_ALGORITHM)
    plaintext = _decrypt(
        ciphertext,
        decryptor=lambda token: secret_box.decrypt(token.decode("ascii")).encode("utf-8"),
    )
    return _restore_sensitive(
        public,
        plaintext,
        _REDISPATCH_FIELDS,
    )


def _split_sensitive(
    payload: dict[str, Any],
    fields: tuple[str, str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if ENVELOPE_FIELD in payload:
        raise TaskPayloadEnvelopeError("task payload already contains a sensitive envelope")
    public = dict(payload)
    unexpected = sorted(_SENSITIVE_FIELD_NAMES.difference(fields).intersection(public))
    if unexpected:
        raise TaskPayloadEnvelopeError(f"unexpected sensitive fields: {','.join(unexpected)}")
    sensitive: dict[str, Any] = {}
    for field in fields:
        value = public.pop(field, {})
        if not isinstance(value, dict):
            raise TaskPayloadEnvelopeError(f"{field} must be an object before encryption")
        sensitive[field] = value
    return sensitive, public


def _take_envelope(
    payload: dict[str, Any],
    sensitive_fields: tuple[str, str],
) -> tuple[object, dict[str, Any]]:
    leaked_fields = [field for field in sensitive_fields if field in payload]
    if leaked_fields:
        raise TaskPayloadEnvelopeError(f"plaintext sensitive fields are forbidden: {','.join(leaked_fields)}")
    public = dict(payload)
    try:
        envelope = public.pop(ENVELOPE_FIELD)
    except KeyError as exc:
        raise TaskPayloadEnvelopeError("sensitive payload envelope is required") from exc
    return envelope, public


def _require_envelope(envelope: object, *, kind: str, algorithm: str) -> str:
    parsed = _parse_envelope(envelope)
    if parsed.get("version") != ENVELOPE_VERSION:
        raise TaskPayloadEnvelopeError("unsupported sensitive payload envelope version")
    if parsed.get("kind") != kind or parsed.get("algorithm") != algorithm:
        raise TaskPayloadEnvelopeError("sensitive payload envelope key domain mismatch")
    ciphertext = parsed.get("ciphertext")
    if not isinstance(ciphertext, str) or not ciphertext:
        raise TaskPayloadEnvelopeError("sensitive payload envelope ciphertext is invalid")
    return ciphertext


def _parse_envelope(envelope: object) -> dict[str, Any]:
    if isinstance(envelope, bytes):
        envelope = envelope.decode("utf-8")
    if isinstance(envelope, str):
        try:
            envelope = json.loads(envelope)
        except json.JSONDecodeError as exc:
            raise TaskPayloadEnvelopeError("sensitive payload envelope is not valid JSON") from exc
    if not isinstance(envelope, dict):
        raise TaskPayloadEnvelopeError("sensitive payload envelope must be an object")
    return envelope


def _encode_envelope(*, kind: str, algorithm: str, ciphertext: str) -> str:
    return json.dumps(
        {
            "algorithm": algorithm,
            "ciphertext": ciphertext,
            "kind": kind,
            "version": ENVELOPE_VERSION,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _restore_sensitive(
    public: dict[str, Any],
    plaintext: bytes,
    fields: tuple[str, str],
) -> dict[str, Any]:
    try:
        sensitive = json.loads(plaintext)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TaskPayloadDecryptionError("decrypted sensitive payload is not valid JSON") from exc
    expected_fields = {*fields, _PUBLIC_BINDING_FIELD}
    if not isinstance(sensitive, dict) or set(sensitive) != expected_fields:
        raise TaskPayloadDecryptionError("decrypted sensitive payload schema is invalid")
    binding = sensitive.pop(_PUBLIC_BINDING_FIELD)
    expected_binding = _public_binding(public)
    if not isinstance(binding, str) or not hmac.compare_digest(binding, expected_binding):
        raise TaskPayloadDecryptionError("sensitive task payload public binding mismatch")
    restored = dict(public)
    for field in fields:
        value = sensitive[field]
        if not isinstance(value, dict):
            raise TaskPayloadDecryptionError(f"decrypted {field} is not an object")
        restored[field] = value
    return restored


def _public_binding(public: dict[str, Any]) -> str:
    return hashlib.sha256(_encode(public)).hexdigest()


def _ready_execution_public(public: dict[str, Any]) -> dict[str, Any]:
    return {field: value for field, value in public.items() if field not in _READY_SETTLEMENT_FIELDS}


def _normalize_ready_public(public: dict[str, Any]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for field, value in public.items():
        name = str(field)
        if name in normalized:
            raise TaskPayloadEnvelopeError("ready payload contains colliding field names")
        if isinstance(value, bytes):
            try:
                normalized[name] = value.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise TaskPayloadEnvelopeError("ready payload bytes must be valid UTF-8") from exc
        elif isinstance(value, str):
            normalized[name] = value
        else:
            normalized[name] = _encode(value).decode("utf-8")
    return normalized


def _decrypt(ciphertext: str, *, decryptor: Callable[[bytes], bytes]) -> bytes:
    try:
        return decryptor(ciphertext.encode("ascii"))
    except (InvalidToken, UnicodeEncodeError, ValueError) as exc:
        raise TaskPayloadDecryptionError("sensitive task payload authentication failed") from exc


def _worker_fernet(worker_id: str, worker_secret: str) -> Fernet:
    identity = worker_id.strip()
    secret_bytes = worker_secret.encode("utf-8")
    if not identity:
        raise TaskPayloadEnvelopeError("worker_id is required for task payload encryption")
    if len(secret_bytes) < _MIN_WORKER_SECRET_BYTES:
        raise TaskPayloadEnvelopeError("worker secret is too short for task payload encryption")
    key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=f"antcode/task-ready/v{ENVELOPE_VERSION}/{identity}".encode(),
    ).derive(secret_bytes)
    return Fernet(base64.urlsafe_b64encode(key))


def _encode(value: Any) -> bytes:
    try:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise TaskPayloadEnvelopeError("sensitive task payload is not JSON serializable") from exc
    return text.encode("utf-8")


__all__ = [
    "ENVELOPE_FIELD",
    "TaskPayloadDecryptionError",
    "TaskPayloadEnvelopeError",
    "open_ready_payload",
    "open_redispatch_payload",
    "seal_ready_payload",
    "seal_redispatch_payload",
]
