"""Strict JSON wire codec for Worker capability snapshots."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

MAX_CAPABILITY_COUNT = 16
MAX_CAPABILITY_NAME_LENGTH = 64
MAX_CAPABILITY_VALUE_BYTES = 4096
MAX_TASK_TYPE_COUNT = 16
MAX_TASK_TYPE_LENGTH = 64


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _validate_capability_names(capabilities: Mapping[Any, Any]) -> None:
    if len(capabilities) > MAX_CAPABILITY_COUNT:
        raise ValueError("too many capabilities")
    if any(not isinstance(name, str) or not name or len(name) > MAX_CAPABILITY_NAME_LENGTH for name in capabilities):
        raise ValueError("capability names must be non-empty strings within the size limit")


def _require_standard_json(value: object) -> None:
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("capability value must be finite standard JSON") from exc


def validate_capabilities(capabilities: object) -> dict[str, Any]:
    if not isinstance(capabilities, dict):
        raise ValueError("capabilities must be a JSON object")
    _validate_capability_names(capabilities)
    task_types = capabilities.get("task_types")
    if task_types is not None and (
        not isinstance(task_types, list)
        or len(task_types) > MAX_TASK_TYPE_COUNT
        or not all(isinstance(task_type, str) and task_type for task_type in task_types)
        or any(len(task_type) > MAX_TASK_TYPE_LENGTH for task_type in task_types)
    ):
        raise ValueError("task_types capability is invalid")
    for value in capabilities.values():
        _require_standard_json(value)
    return capabilities


def encode_capabilities(capabilities: object) -> dict[str, str]:
    if capabilities is None:
        return {}
    validated = validate_capabilities(capabilities)
    encoded = {
        name: json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)
        for name, value in validated.items()
    }
    if any(len(value.encode("utf-8")) > MAX_CAPABILITY_VALUE_BYTES for value in encoded.values()):
        raise ValueError("capability value exceeds the size limit")
    return encoded


def decode_capabilities(encoded: Mapping[str, str]) -> dict[str, Any]:
    _validate_capability_names(encoded)
    if any(len(value.encode("utf-8")) > MAX_CAPABILITY_VALUE_BYTES for value in encoded.values()):
        raise ValueError("capability value exceeds the size limit")
    try:
        decoded = {name: json.loads(value, parse_constant=_reject_json_constant) for name, value in encoded.items()}
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("capability value is invalid JSON") from exc
    return validate_capabilities(decoded)


__all__ = [
    "MAX_CAPABILITY_COUNT",
    "MAX_CAPABILITY_NAME_LENGTH",
    "MAX_CAPABILITY_VALUE_BYTES",
    "MAX_TASK_TYPE_COUNT",
    "MAX_TASK_TYPE_LENGTH",
    "decode_capabilities",
    "encode_capabilities",
    "validate_capabilities",
]
