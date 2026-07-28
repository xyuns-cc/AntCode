"""日志消息、结构化字段和异常堆栈脱敏。"""

from __future__ import annotations

import re
import traceback
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from loguru import Record


SENSITIVE_PATTERNS = [
    (re.compile(r'("api[_-]?key"\s*:\s*")[^"]+(")', re.IGNORECASE), r"\1***\2"),
    (
        re.compile(r'("(?:access_|refresh_)?token"\s*:\s*")[^"]+(")', re.IGNORECASE),
        r"\1***\2",
    ),
    (
        re.compile(
            r'("(?:secret|password|passwd|pwd|install_key|private_key)"\s*:\s*")[^"]+(")',
            re.IGNORECASE,
        ),
        r"\1***\2",
    ),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._\-]+", re.IGNORECASE), "Bearer ***"),
    (re.compile(r"\bBasic\s+[A-Za-z0-9+/=]+", re.IGNORECASE), "Basic ***"),
    (re.compile(r"\bak_[A-Za-z0-9_\-]{8,}"), "ak_***"),
    (
        re.compile(
            r'(api[_-]?key|secret[_-]?key|access[_-]?key)["\']?\s*[:=]\s*["\']?([a-zA-Z0-9_\-]{8,})["\']?',
            re.IGNORECASE,
        ),
        r"\1=***REDACTED***",
    ),
    (
        re.compile(
            r'(password|passwd|pwd)["\']?\s*[:=]\s*["\']?([^"\'\s,}&#]{3,})["\']?',
            re.IGNORECASE,
        ),
        r"\1=***REDACTED***",
    ),
    (
        re.compile(
            r'(token|jwt)["\']?\s*[:=]\s*["\']?([a-zA-Z0-9_\-\.]{20,})["\']?',
            re.IGNORECASE,
        ),
        r"\1=***REDACTED***",
    ),
    (
        re.compile(
            r'(Authorization)["\']?\s*[:=]\s*["\']?(?:Bearer\s+|Basic\s+)?([a-zA-Z0-9_\-\.+/=]{8,})["\']?',
            re.IGNORECASE,
        ),
        r"\1=***REDACTED***",
    ),
    (
        re.compile(
            r"((?:redis|rediss)\+sentinel://)[^@/\s]+@([^@/\s]+@)",
            re.IGNORECASE,
        ),
        r"\1***@\2",
    ),
    (
        re.compile(
            r"((?:mysql|postgres|postgresql|redis(?:s|\+sentinel|\+cluster)?|rediss\+(?:sentinel|cluster))://[^:@/\s]+):[^@\s]+@",
            re.IGNORECASE,
        ),
        r"\1:***@",
    ),
    (
        re.compile(r"([?&][^=&#\s]*(?:password|passwd|pwd)=)[^&#\s]+", re.IGNORECASE),
        r"\1***",
    ),
    (re.compile(r"([a-zA-Z0-9_.+-]+)@([a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)"), r"***@\2"),
]

DEFAULT_SENSITIVE_KEY_TOKENS: frozenset[str] = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "secret_key",
        "secretkey",
        "access_key",
        "accesskey",
        "authorization",
        "auth",
        "credential",
        "credentials",
        "jwt",
        "cert",
        "certificate",
        "private_key",
        "install_key",
        "gateway_token",
        "worker_install_key",
        "encryption_key",
        "jwt_secret",
        "bearer",
        "redis_password",
        "client_key",
        "cookie",
        "session",
    }
)


def sanitize_log_message(message: str) -> str:
    """对日志消息进行敏感信息脱敏。"""
    sanitized = message
    for pattern, replacement in SENSITIVE_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)
    return sanitized


def sanitize_dict(
    data: dict[str, Any],
    sensitive_keys: set[str] | frozenset[str] | None = None,
    mask: str = "***REDACTED***",
) -> dict[str, Any]:
    """递归脱敏结构化日志字段，不修改输入字典。"""
    keys = DEFAULT_SENSITIVE_KEY_TOKENS if sensitive_keys is None else sensitive_keys
    result: dict[str, Any] = {}
    for key, value in data.items():
        if any(token in str(key).lower() for token in keys):
            result[key] = mask
        elif isinstance(value, dict):
            result[key] = sanitize_dict(value, keys, mask)
        elif isinstance(value, list):
            result[key] = [_sanitize_sequence_item(item, keys, mask) for item in value]
        elif isinstance(value, str):
            result[key] = sanitize_log_message(value)
        else:
            result[key] = value
    return result


def _sanitize_sequence_item(
    item: Any,
    sensitive_keys: set[str] | frozenset[str],
    mask: str,
) -> Any:
    if isinstance(item, dict):
        return sanitize_dict(item, sensitive_keys, mask)
    if isinstance(item, str):
        return sanitize_log_message(item)
    return item


def _inject_trace_id(record: Record) -> None:
    extra = record.setdefault("extra", {})
    if extra.get("trace_id"):
        return
    try:
        from antcode_core.observability.tracing import get_current_trace_id

        trace_id = get_current_trace_id()
    except Exception:  # noqa: BLE001 - 日志链路故障不能遮蔽原始业务错误
        trace_id = None
    extra["trace_id"] = trace_id or "-"


def _sanitize_exception(record: Record) -> None:
    exception = record.get("exception")
    if exception is None or exception.type is None or exception.value is None:
        return
    rendered = traceback.TracebackException(
        exception.type,
        exception.value,
        exception.traceback,
        capture_locals=False,
    ).format(chain=True)
    safe_traceback = sanitize_log_message("".join(rendered).rstrip())
    record["message"] = f"{record.get('message', '')}\n{safe_traceback}"
    record["exception"] = None


class SanitizingFilter:
    """注入 trace id，并脱敏消息、结构化字段和异常堆栈。"""

    def __call__(self, record: Record) -> bool:
        _inject_trace_id(record)
        record["message"] = sanitize_log_message(record.get("message", ""))
        if isinstance(record.get("extra"), dict):
            record["extra"] = sanitize_dict(record["extra"])
        record["extra"].setdefault("trace_id", "-")
        _sanitize_exception(record)
        return True
