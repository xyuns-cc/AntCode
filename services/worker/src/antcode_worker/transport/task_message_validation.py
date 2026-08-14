"""Shared boundary validation for incoming Worker task messages."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

DEFAULT_TASK_TIMEOUT_SECONDS = 3600
MAX_TASK_TIMEOUT_SECONDS = 86400
_RESERVED_ENV_NAMES = frozenset(
    {
        "ANTCODE_RUNTIME_ENV",
        "ANTCODE_TRACE_ID",
        "BASH_ENV",
        "ENV",
        "PATH",
        "PYTHONHOME",
        "PYTHONPATH",
        "TRACEPARENT",
        "VIRTUAL_ENV",
    }
)
_RESERVED_ENV_PREFIXES = ("ANTCODE_SPIDER_", "DYLD_", "LD_")


@dataclass(frozen=True, slots=True)
class ValidatedTaskFields:
    task_id: str
    project_id: str
    run_id: str
    project_type: str
    priority: int
    params: dict[str, Any]
    environment: dict[str, str]
    timeout: int
    source_subdir: str
    entry_point: str
    runtime_env_name: str


def validate_task_message_payload(
    payload: Mapping[str, Any],
    *,
    allow_integer_strings: bool = False,
) -> ValidatedTaskFields:
    """Normalize a transport payload or reject malformed external input."""
    fields = _public_fields(payload, allow_integer_strings)
    return ValidatedTaskFields(
        **fields,
        params=_mapping(payload, "params"),
        environment=validate_task_environment(_mapping(payload, "environment")),
    )


def validate_task_message_public_fields(
    payload: Mapping[str, Any],
    *,
    allow_integer_strings: bool = False,
) -> None:
    """Validate non-sensitive fields before opening the encrypted envelope."""
    _public_fields(payload, allow_integer_strings)


def _public_fields(payload: Mapping[str, Any], allow_integer_strings: bool) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise TypeError("任务消息必须是对象")
    if "project_path" in payload:
        raise ValueError("project_path 已废弃，任务必须使用 source_bundle")
    return {
        "task_id": _required_text(payload, "task_id"),
        "project_id": _required_text(payload, "project_id"),
        "run_id": _required_text(payload, "run_id"),
        "project_type": _optional_text(payload, "project_type", "code"),
        "priority": _integer(
            payload,
            "priority",
            0,
            allow_integer_strings=allow_integer_strings,
        ),
        "timeout": _timeout(payload, allow_integer_strings),
        "source_subdir": _optional_text(payload, "source_subdir", ""),
        "entry_point": _optional_text(payload, "entry_point", ""),
        "runtime_env_name": _optional_text(payload, "runtime_env_name", ""),
    }


def _required_text(payload: Mapping[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} 必须是非空字符串")
    return value


def _optional_text(payload: Mapping[str, Any], field: str, default: str) -> str:
    value = payload.get(field, default)
    if value is None:
        return default
    if not isinstance(value, str):
        raise TypeError(f"{field} 必须是字符串")
    return value


def _mapping(payload: Mapping[str, Any], field: str) -> dict[str, Any]:
    value = payload.get(field, {})
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} 必须是对象")
    return dict(value)


def validate_task_environment(environment: Mapping[str, Any]) -> dict[str, str]:
    """Accept user variables verbatim or reject reserved/invalid entries."""
    validated: dict[str, str] = {}
    for key, value in environment.items():
        if not isinstance(key, str) or not key or "=" in key or "\x00" in key:
            raise ValueError("environment 变量名必须是非空且不含 '=' 或 NUL 的字符串")
        if key in _RESERVED_ENV_NAMES or key.startswith(_RESERVED_ENV_PREFIXES):
            raise ValueError(f"environment 变量 {key!r} 属于 Worker 保留命名空间")
        if not isinstance(value, str) or "\x00" in value:
            raise ValueError(f"environment[{key!r}] 必须是不含 NUL 的字符串")
        validated[key] = value
    return validated


def _integer(
    payload: Mapping[str, Any],
    field: str,
    default: int,
    *,
    allow_integer_strings: bool,
) -> int:
    value = payload.get(field, default)
    if type(value) is int:
        return value
    if allow_integer_strings and isinstance(value, str) and re.fullmatch(r"-?\d+", value):
        return int(value)
    raise TypeError(f"{field} 必须是整数")


def _timeout(payload: Mapping[str, Any], allow_integer_strings: bool) -> int:
    value = _integer(
        payload,
        "timeout",
        DEFAULT_TASK_TIMEOUT_SECONDS,
        allow_integer_strings=allow_integer_strings,
    )
    if not 1 <= value <= MAX_TASK_TIMEOUT_SECONDS:
        raise ValueError(f"timeout 必须在 1..{MAX_TASK_TIMEOUT_SECONDS} 秒之间")
    return value


__all__ = [
    "MAX_TASK_TIMEOUT_SECONDS",
    "ValidatedTaskFields",
    "validate_task_message_payload",
    "validate_task_message_public_fields",
    "validate_task_environment",
]
