"""Shared UTF-8 size contract for persisted runtime manifest metadata.

The runtime-control result frame is capped at 1 MiB.  A Worker normally keeps
at most 100 managed environments, so 128-byte keys plus 1 KiB descriptions
leave ample room for the rest of the list response.  Limits are bytes, not
Python characters, because JSON and transport limits are also byte based.
"""

from __future__ import annotations

import re

RUNTIME_KEY_MAX_BYTES = 128
RUNTIME_DESCRIPTION_MAX_BYTES = 1024
RUNTIME_CREATED_BY_MAX_BYTES = 200
RUNTIME_OWNER_USER_ID_MAX_BYTES = 32
RUNTIME_MANIFEST_MAX_BYTES = 1024 * 1024

# Compatibility names for Pydantic's character-count fast rejection.  The
# byte validators below remain authoritative for non-ASCII input.
RUNTIME_KEY_MAX_LENGTH = RUNTIME_KEY_MAX_BYTES
RUNTIME_DESCRIPTION_MAX_LENGTH = RUNTIME_DESCRIPTION_MAX_BYTES
_DECIMAL_ID_PATTERN = re.compile(r"^[0-9]+$")


def _validate_optional_text(value: str | None, field: str, max_bytes: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"runtime {field} 必须是字符串或 null")
    if len(value.encode("utf-8")) > max_bytes:
        raise ValueError(f"runtime {field} UTF-8 长度不能超过 {max_bytes} 字节")
    return value.strip()


def validate_runtime_key(value: str | None) -> str | None:
    return _validate_optional_text(value, "key", RUNTIME_KEY_MAX_BYTES)


def validate_runtime_description(value: str | None) -> str | None:
    return _validate_optional_text(value, "description", RUNTIME_DESCRIPTION_MAX_BYTES)


def validate_runtime_metadata(key: str | None, description: str | None) -> tuple[str | None, str | None]:
    return validate_runtime_key(key), validate_runtime_description(description)


def validate_runtime_creator(
    created_by: str | None,
    owner_user_id: str | None,
) -> tuple[str | None, str | None]:
    normalized_creator = _validate_optional_text(created_by, "created_by", RUNTIME_CREATED_BY_MAX_BYTES)
    normalized_owner = _validate_optional_text(owner_user_id, "owner_user_id", RUNTIME_OWNER_USER_ID_MAX_BYTES)
    if normalized_owner and not _DECIMAL_ID_PATTERN.fullmatch(normalized_owner):
        raise ValueError("runtime owner_user_id 必须是十进制用户 ID")
    if normalized_owner and int(normalized_owner) <= 0:
        raise ValueError("runtime owner_user_id 必须大于 0")
    return normalized_creator, normalized_owner


__all__ = [
    "RUNTIME_CREATED_BY_MAX_BYTES",
    "RUNTIME_DESCRIPTION_MAX_BYTES",
    "RUNTIME_DESCRIPTION_MAX_LENGTH",
    "RUNTIME_KEY_MAX_BYTES",
    "RUNTIME_KEY_MAX_LENGTH",
    "RUNTIME_MANIFEST_MAX_BYTES",
    "RUNTIME_OWNER_USER_ID_MAX_BYTES",
    "validate_runtime_creator",
    "validate_runtime_description",
    "validate_runtime_key",
    "validate_runtime_metadata",
]
