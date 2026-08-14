"""Encrypted task construction for the in-process contract Gateway."""

from __future__ import annotations

import json
from typing import Any

from antcode_contracts import data_pb2
from antcode_core.common.security.task_payload_envelope import seal_ready_payload

_EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def build_task_dispatch(
    payload: dict[str, Any],
    *,
    worker_id: str,
    worker_secret: str,
    receipt_id: str,
) -> data_pb2.TaskDispatch:
    digest = str(payload.get("source_bundle_sha256") or _EMPTY_SHA256)
    ready_payload = _ready_payload(payload, digest)
    sealed = seal_ready_payload(
        ready_payload,
        worker_id=worker_id,
        worker_secret=worker_secret,
    )
    return data_pb2.TaskDispatch(
        task_id=str(payload.get("task_id", "")),
        project_id=str(payload.get("project_id", "")),
        project_type=str(payload.get("project_type", "code")),
        priority=int(payload.get("priority", 0) or 0),
        timeout_seconds=int(ready_payload["timeout"] or 3600),
        source_bundle_uri=str(ready_payload["source_bundle_uri"]),
        source_bundle_sha256=digest,
        source_bundle_size=int(payload.get("source_bundle_size", 0) or 0),
        transfer_method=str(ready_payload["transfer_method"]),
        resolved_revision=str(ready_payload["resolved_revision"]),
        source_subdir=str(payload.get("source_subdir", "")),
        entry_point=str(payload.get("entry_point", "")),
        run_id=str(payload.get("run_id", "")),
        receipt_id=receipt_id,
        runtime_env_name=str(payload.get("runtime_env_name", "")),
        sealed_ready_payload=_encode(sealed),
    )


def _ready_payload(payload: dict[str, Any], digest: str) -> dict[str, Any]:
    ready = {
        **payload,
        "timeout": payload.get("timeout_seconds", payload.get("timeout", 3600)),
        "source_bundle_uri": str(payload.get("source_bundle_uri") or f"pgartifact://{digest}"),
        "source_bundle_sha256": digest,
        "transfer_method": str(payload.get("transfer_method", "source_bundle")),
        "resolved_revision": str(payload.get("resolved_revision", "contract-test")),
    }
    ready.pop("timeout_seconds", None)
    return ready


def _encode(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


__all__ = ["build_task_dispatch"]
