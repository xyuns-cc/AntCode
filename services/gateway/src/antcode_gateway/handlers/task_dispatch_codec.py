"""Ready-stream task model and gRPC TaskDispatch transcoding."""

from __future__ import annotations

from dataclasses import dataclass

from antcode_contracts import data_pb2
from antcode_core.observability.tracing import inject_trace


@dataclass
class TaskInfo:
    """Validated internal task representation consumed by Gateway dispatch."""

    task_id: str
    project_id: str
    run_id: str = ""
    project_type: str = "spider"
    priority: int = 0
    timeout: int = 3600
    source_bundle_uri: str = ""
    source_bundle_sha256: str = ""
    source_bundle_size: int = 0
    transfer_method: str = ""
    resolved_revision: str = ""
    source_subdir: str = ""
    entry_point: str = ""
    runtime_env_name: str = ""
    sealed_ready_payload: bytes = b""
    receipt_id: str = ""
    trace_parent: str = ""


def task_info_to_dispatch(task: TaskInfo) -> data_pb2.TaskDispatch:
    """Transcode a validated Redis task to the Worker protobuf contract."""
    source_uri = task.source_bundle_uri or ""
    source_size = int(task.source_bundle_size or 0)
    dispatch = data_pb2.TaskDispatch(
        task_id=task.task_id,
        project_id=task.project_id,
        project_type=task.project_type,
        priority=int(task.priority),
        timeout_seconds=int(task.timeout),
        source_bundle_uri=source_uri,
        source_bundle_sha256=task.source_bundle_sha256 or "",
        source_bundle_size=source_size,
        transfer_method=task.transfer_method or ("source_bundle" if source_uri else ""),
        resolved_revision=task.resolved_revision or "",
        source_subdir=task.source_subdir or "",
        entry_point=task.entry_point,
        run_id=task.run_id,
        receipt_id=task.receipt_id,
        runtime_env_name=task.runtime_env_name,
        sealed_ready_payload=task.sealed_ready_payload,
    )
    inject_trace(dispatch, traceparent=task.trace_parent or "")
    return dispatch


__all__ = ["TaskInfo", "task_info_to_dispatch"]
