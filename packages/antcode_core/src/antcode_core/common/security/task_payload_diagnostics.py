"""Safe diagnostic projection for poison task frames."""

from __future__ import annotations

import hashlib
import json

_DIAGNOSTIC_FIELDS = frozenset(
    {
        "dispatch_id",
        "dispatch_lease_gen",
        "dispatch_lease_id",
        "entry_point",
        "priority",
        "project_id",
        "project_path",
        "project_type",
        "resolved_revision",
        "run_id",
        "runtime_env_name",
        "source_bundle_sha256",
        "source_bundle_size",
        "source_bundle_uri",
        "source_subdir",
        "task_id",
        "timeout",
        "trace_parent",
        "transfer_method",
    }
)


def redact_persisted_task_frame(payload: dict[str, str]) -> dict[str, str]:
    """Keep only public diagnostics and a digest when persisting a poison frame."""
    diagnostic = {key: value for key, value in payload.items() if key in _DIAGNOSTIC_FIELDS}
    diagnostic["payload_sha256"] = _payload_digest(payload)
    return diagnostic


def _payload_digest(payload: dict[str, str]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = ["redact_persisted_task_frame"]
