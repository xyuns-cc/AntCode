"""统一的持久错误消息安全边界。"""

from __future__ import annotations

from antcode_core.common.log_sanitization import sanitize_log_message

MAX_PERSISTED_ERROR_MESSAGE_BYTES = 16 * 1024
ERROR_MESSAGE_TRUNCATION_SUFFIX = "\n...[error message truncated]"


def normalize_persisted_error_message(
    message: object | None,
    *,
    max_bytes: int = MAX_PERSISTED_ERROR_MESSAGE_BYTES,
) -> str | None:
    """先脱敏，再按 UTF-8 字节限制待持久化或对外返回的错误消息。"""
    if message is None:
        return None
    sanitized = sanitize_log_message(str(message))
    encoded = sanitized.encode("utf-8")
    if len(encoded) <= max_bytes:
        return sanitized
    suffix = ERROR_MESSAGE_TRUNCATION_SUFFIX.encode("utf-8")
    if max_bytes < len(suffix):
        raise ValueError("error message byte limit is smaller than truncation suffix")
    prefix = encoded[: max_bytes - len(suffix)].decode("utf-8", errors="ignore")
    return f"{prefix}{ERROR_MESSAGE_TRUNCATION_SUFFIX}"


__all__ = [
    "ERROR_MESSAGE_TRUNCATION_SUFFIX",
    "MAX_PERSISTED_ERROR_MESSAGE_BYTES",
    "normalize_persisted_error_message",
]
