"""Worker-scoped runtime-control identifiers used by Redis ACL rules."""

from __future__ import annotations

import re

_WORKER_KEY_SEGMENT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
_RUNTIME_REQUEST_NONCE_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def validate_worker_key_segment(worker_id: str) -> str:
    """Validate a Worker ID before embedding it in an ACL-scoped Redis key."""
    if not _WORKER_KEY_SEGMENT_PATTERN.fullmatch(worker_id):
        raise ValueError("worker_id 必须为 1-32 位字母、数字、点、下划线或连字符")
    return worker_id


def runtime_control_request_id(worker_id: str, nonce: str) -> str:
    """Build a Worker-scoped runtime request ID from a UUID hex nonce."""
    worker_segment = validate_worker_key_segment(worker_id)
    if not _RUNTIME_REQUEST_NONCE_PATTERN.fullmatch(nonce):
        raise ValueError("运行时控制 request nonce 必须为 32 位小写十六进制")
    return f"{worker_segment}:{nonce}"


def require_runtime_control_request_id(request_id: str, worker_id: str) -> str:
    """Require a runtime request ID to belong to the declared Worker."""
    worker_segment = validate_worker_key_segment(worker_id)
    prefix = f"{worker_segment}:"
    if not request_id.startswith(prefix):
        raise ValueError("运行时控制 request_id 不属于当前 Worker")
    runtime_control_request_id(worker_segment, request_id[len(prefix) :])
    return request_id


__all__ = [
    "require_runtime_control_request_id",
    "runtime_control_request_id",
    "validate_worker_key_segment",
]
