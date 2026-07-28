"""Validation for serialized Worker credentials."""

from __future__ import annotations

import json
from typing import Any

_REQUIRED_FIELDS = {"worker_id", "api_key", "secret_key"}
_ALLOWED_FIELDS = _REQUIRED_FIELDS | {
    "gateway_host",
    "gateway_port",
    "redis_username",
    "redis_password",
    "registration_id",
    "registered_at",
}


def validate_credentials(credentials: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(credentials, dict):
        raise ValueError("Worker 凭证必须是 JSON object")
    unknown_fields = set(credentials) - _ALLOWED_FIELDS
    if unknown_fields:
        raise ValueError(f"Worker 凭证包含未知字段: {sorted(unknown_fields)}")
    missing_fields = [name for name in _REQUIRED_FIELDS if not credentials.get(name)]
    if missing_fields:
        raise ValueError(f"Worker 凭证缺少必填字段: {sorted(missing_fields)}")
    payload = dict(credentials)
    _validate_string_fields(payload)
    if bool(payload.get("redis_username")) != bool(payload.get("redis_password")):
        raise ValueError("Worker Redis 用户名和密码必须成对存在")
    registered_at = payload.get("registered_at")
    if registered_at is not None and not isinstance(registered_at, str):
        raise ValueError("Worker 凭证 registered_at 必须是字符串")
    _validate_gateway_port(payload.get("gateway_port", 0))
    return payload


def decode_credentials(content: str) -> dict[str, Any]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("Worker 凭证文件不是有效 JSON") from exc
    return validate_credentials(payload)


def _validate_string_fields(payload: dict[str, Any]) -> None:
    fields = _REQUIRED_FIELDS | {"gateway_host", "redis_username", "redis_password", "registration_id"}
    for field_name in fields:
        if not isinstance(payload.get(field_name, ""), str):
            raise ValueError(f"Worker 凭证 {field_name} 必须是字符串")


def _validate_gateway_port(gateway_port: Any) -> None:
    if isinstance(gateway_port, bool) or not isinstance(gateway_port, int):
        raise ValueError("Worker 凭证 gateway_port 必须是整数")
    if gateway_port and not 1 <= gateway_port <= 65535:
        raise ValueError("Worker 凭证 gateway_port 超出范围")


__all__ = ["decode_credentials", "validate_credentials"]
