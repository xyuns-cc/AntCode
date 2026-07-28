"""Deterministic credentials for recoverable Worker registration."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

REGISTRATION_PROTOCOL_VERSION = 2
CREDENTIAL_DERIVATION_VERSION = 1
REGISTRATION_ID_HEX_LENGTH = 32
RECOVERY_SECRET_HEX_LENGTH = 64
_INSTALL_KEY_HASH_HEX_LENGTH = 64
_DERIVED_CREDENTIAL_BYTES = 48
_API_KEY_BYTES = 16
_HEX_PATTERN = re.compile(r"^[0-9a-f]+$")


@dataclass(frozen=True)
class DerivedWorkerCredentials:
    api_key: str
    secret_key: str


def derive_worker_credentials(
    install_key_hash: str,
    registration_id: str,
    recovery_secret: str,
    *,
    version: int = CREDENTIAL_DERIVATION_VERSION,
) -> DerivedWorkerCredentials:
    """Derive stable, domain-separated credentials from client entropy."""
    key_hash_bytes = _decode_hex("install_key_hash", install_key_hash, _INSTALL_KEY_HASH_HEX_LENGTH)
    registration_id_bytes = _decode_hex("registration_id", registration_id, REGISTRATION_ID_HEX_LENGTH)
    recovery_secret_bytes = _decode_hex("recovery_secret", recovery_secret, RECOVERY_SECRET_HEX_LENGTH)
    if version != CREDENTIAL_DERIVATION_VERSION:
        raise ValueError(f"不支持的 Worker 凭据派生版本: {version}")
    info = b"antcode-worker-registration-v1\x00" + registration_id_bytes
    material = HKDF(
        algorithm=hashes.SHA256(),
        length=_DERIVED_CREDENTIAL_BYTES,
        salt=key_hash_bytes,
        info=info,
    ).derive(recovery_secret_bytes)
    return DerivedWorkerCredentials(
        api_key=material[:_API_KEY_BYTES].hex(),
        secret_key=material[_API_KEY_BYTES:].hex(),
    )


def hash_recovery_secret(recovery_secret: str) -> str:
    secret_bytes = _decode_hex("recovery_secret", recovery_secret, RECOVERY_SECRET_HEX_LENGTH)
    return hashlib.sha256(secret_bytes).hexdigest()


def _decode_hex(name: str, value: str, expected_length: int) -> bytes:
    normalized = value or ""
    if len(normalized) != expected_length or _HEX_PATTERN.fullmatch(normalized) is None:
        raise ValueError(f"{name} 必须是 {expected_length} 位十六进制字符串")
    return bytes.fromhex(normalized)


__all__ = [
    "CREDENTIAL_DERIVATION_VERSION",
    "DerivedWorkerCredentials",
    "REGISTRATION_ID_HEX_LENGTH",
    "REGISTRATION_PROTOCOL_VERSION",
    "RECOVERY_SECRET_HEX_LENGTH",
    "derive_worker_credentials",
    "hash_recovery_secret",
]
