"""Persisted ENCRYPTION_KEY rotation storage contract."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

TEXT_CIPHERTEXT_PREFIX = "enc:v1:"
JSON_CIPHERTEXT_KEY = "__antcode_encrypted_v1__"


class CiphertextFormat(StrEnum):
    DIRECT = "direct"
    TEXT_V1 = "text-v1"
    JSON_V1 = "json-v1"


@dataclass(frozen=True)
class EncryptedFieldSpec:
    column: str
    storage_format: CiphertextFormat


@dataclass(frozen=True)
class EncryptedTableSpec:
    table: str
    fields: tuple[EncryptedFieldSpec, ...]


@dataclass(frozen=True)
class TableRotationResult:
    table: str
    rows_scanned: int
    ciphertexts_scanned: int
    ciphertexts_requiring_rotation: int
    rows_rewritten: int


GLOBAL_ENCRYPTED_TABLES = (
    EncryptedTableSpec(
        "git_credentials",
        (EncryptedFieldSpec("secret_encrypted", CiphertextFormat.DIRECT),),
    ),
    EncryptedTableSpec(
        "system_configs",
        (EncryptedFieldSpec("config_value", CiphertextFormat.TEXT_V1),),
    ),
    EncryptedTableSpec(
        "project_files",
        (
            EncryptedFieldSpec("runtime_config", CiphertextFormat.JSON_V1),
            EncryptedFieldSpec("environment_vars", CiphertextFormat.JSON_V1),
        ),
    ),
    EncryptedTableSpec(
        "project_codes",
        (
            EncryptedFieldSpec("runtime_config", CiphertextFormat.JSON_V1),
            EncryptedFieldSpec("environment_vars", CiphertextFormat.JSON_V1),
        ),
    ),
    EncryptedTableSpec(
        "project_rules",
        (
            EncryptedFieldSpec("headers", CiphertextFormat.JSON_V1),
            EncryptedFieldSpec("cookies", CiphertextFormat.JSON_V1),
            EncryptedFieldSpec("proxy_config", CiphertextFormat.JSON_V1),
            EncryptedFieldSpec("task_config", CiphertextFormat.JSON_V1),
        ),
    ),
    EncryptedTableSpec(
        "scheduled_tasks",
        (
            EncryptedFieldSpec("execution_params", CiphertextFormat.JSON_V1),
            EncryptedFieldSpec("environment_vars", CiphertextFormat.JSON_V1),
        ),
    ),
)

_EXPECTED_TABLES = frozenset(
    {
        "git_credentials",
        "system_configs",
        "project_files",
        "project_codes",
        "project_rules",
        "scheduled_tasks",
    }
)


def validate_rotation_contract() -> None:
    tables = [spec.table for spec in GLOBAL_ENCRYPTED_TABLES]
    if len(tables) != len(set(tables)) or set(tables) != _EXPECTED_TABLES:
        raise RuntimeError("全域密钥轮换表清单不完整或重复")
    for spec in GLOBAL_ENCRYPTED_TABLES:
        columns = [field.column for field in spec.fields]
        if not columns or len(columns) != len(set(columns)):
            raise RuntimeError(f"全域密钥轮换字段清单为空或重复: table={spec.table}")


def decode_ciphertext(value: Any, field: EncryptedFieldSpec, *, context: str) -> str:
    if field.storage_format is CiphertextFormat.DIRECT:
        return _require_token(value, context)
    if field.storage_format is CiphertextFormat.TEXT_V1:
        return _decode_text(value, context)
    return _decode_json(value, context)


def encode_ciphertext(token: str, field: EncryptedFieldSpec) -> str:
    if field.storage_format is CiphertextFormat.DIRECT:
        return token
    if field.storage_format is CiphertextFormat.TEXT_V1:
        return TEXT_CIPHERTEXT_PREFIX + token
    return json.dumps({JSON_CIPHERTEXT_KEY: token}, separators=(",", ":"), sort_keys=True)


def _decode_text(value: Any, context: str) -> str:
    text = _require_token(value, context)
    if not text.startswith(TEXT_CIPHERTEXT_PREFIX):
        raise RuntimeError(f"持久化敏感字段不是受支持的密文格式: {context}")
    return _require_token(text.removeprefix(TEXT_CIPHERTEXT_PREFIX), context)


def _decode_json(value: Any, context: str) -> str:
    parsed = _parse_json(value, context)
    if not isinstance(parsed, dict) or set(parsed) != {JSON_CIPHERTEXT_KEY}:
        raise RuntimeError(f"持久化敏感字段不是受支持的密文格式: {context}")
    return _require_token(parsed[JSON_CIPHERTEXT_KEY], context)


def _parse_json(value: Any, context: str) -> Any:
    if not isinstance(value, (str, bytes, bytearray)):
        return value
    try:
        return json.loads(value)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"持久化敏感字段 JSON envelope 无效: {context}") from exc


def _require_token(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"持久化敏感字段密文无效: {context}")
    return value


__all__ = [
    "CiphertextFormat",
    "EncryptedFieldSpec",
    "EncryptedTableSpec",
    "GLOBAL_ENCRYPTED_TABLES",
    "TableRotationResult",
    "decode_ciphertext",
    "encode_ciphertext",
    "validate_rotation_contract",
]
