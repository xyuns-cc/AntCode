"""Integrity checks for Direct Redis log-ingest frames and batches."""

from __future__ import annotations

from typing import Any

from antcode_contracts import data_pb2
from antcode_core.application.services.workers.log_ingest_generation import (
    stream_id_not_after,
)
from antcode_core.common.log_limits import DEFAULT_LOG_MAX_BATCH_BYTES
from antcode_core.domain.models import TaskRun, TaskRunLeaseGeneration, Worker
from antcode_core.infrastructure.redis.runtime_control_keys import validate_worker_key_segment
from antcode_core.infrastructure.redis.stream_client import PROTO_FIELD, ProtoCodec

from antcode_master.ingester.log_ingest_message import InvalidLogBatchError

MAX_LOG_INGEST_FRAME_BYTES = DEFAULT_LOG_MAX_BATCH_BYTES


class BoundedLogBatchCodec:
    """Decode one strict single-field Redis frame after enforcing its byte cap."""

    def __init__(self, max_frame_bytes: int = MAX_LOG_INGEST_FRAME_BYTES) -> None:
        if max_frame_bytes < 1:
            raise ValueError("log ingest frame 上限必须为正整数")
        self._max_frame_bytes = max_frame_bytes
        self._codec = ProtoCodec(data_pb2.LogBatch)

    def encode(self, message: data_pb2.LogBatch) -> dict[bytes | str, bytes | str]:
        fields = self._codec.encode(message)
        self._require_bounded_frame(fields)
        return fields

    def decode(self, fields: dict[bytes | str, bytes | str]) -> data_pb2.LogBatch:
        self._require_bounded_frame(fields)
        return self._codec.decode(fields)

    def _require_bounded_frame(self, fields: dict[bytes | str, bytes | str]) -> None:
        if not isinstance(fields, dict):
            raise ValueError("log ingest Redis frame 必须是字段映射")
        normalized = {_field_bytes(key) for key in fields}
        if normalized != {PROTO_FIELD}:
            raise ValueError("log ingest Redis frame 必须且只能包含 p 字段")
        frame_bytes = sum(len(_field_bytes(key)) + len(_field_bytes(value)) for key, value in fields.items())
        if frame_bytes > self._max_frame_bytes:
            raise ValueError(f"log ingest Redis frame 超过 {self._max_frame_bytes} bytes")


async def require_log_batch_integrity(log_batch: data_pb2.LogBatch, msg_id: str) -> None:
    """Accept current logs or predecessor backlog bounded by its persisted cutoff."""
    worker_id = _canonical_worker_id(log_batch.worker_id)
    lease_id = _canonical_lease_id(log_batch.lease_id)
    run_ids = _canonical_run_ids(log_batch)
    await _require_run_bindings(worker_id, lease_id, run_ids, msg_id=msg_id)


def _canonical_worker_id(value: str) -> str:
    worker_id = str(value or "")
    if not worker_id or worker_id != worker_id.strip():
        raise InvalidLogBatchError("worker_id 必须为非空规范字符串")
    try:
        return validate_worker_key_segment(worker_id)
    except ValueError as exc:
        raise InvalidLogBatchError("worker_id 格式非法") from exc


def _canonical_lease_id(value: str) -> str:
    lease_id = str(value or "")
    if not lease_id or lease_id != lease_id.strip() or len(lease_id) > 64:
        raise InvalidLogBatchError("lease_id 必须为 1..64 字符的规范字符串")
    return lease_id


def _canonical_run_ids(log_batch: data_pb2.LogBatch) -> set[str]:
    if not log_batch.entries:
        raise InvalidLogBatchError("日志批次 entries 不能为空")
    run_ids = {entry.run_id for entry in log_batch.entries}
    if any(not run_id or run_id != run_id.strip() for run_id in run_ids):
        raise InvalidLogBatchError("run_id 必须为非空规范字符串")
    return run_ids


async def _require_run_bindings(worker_id: str, lease_id: str, run_ids: set[str], *, msg_id: str) -> None:
    worker = await Worker.filter(public_id=worker_id).only("id").first()
    if worker is None:
        raise InvalidLogBatchError("日志批次 Worker 不存在")
    rows = await TaskRun.filter(run_id__in=run_ids).values_list("run_id", "worker_id", "lease_id")
    bindings = {str(run_id): (assigned_worker, stored_lease) for run_id, assigned_worker, stored_lease in rows}
    mismatched = _mismatched_run_bindings(worker.id, lease_id, run_ids, bindings=bindings)
    if mismatched:
        await _require_generation_backlog(worker.id, lease_id, mismatched, msg_id=msg_id)


def _mismatched_run_bindings(
    worker_id: int,
    lease_id: str,
    run_ids: set[str],
    *,
    bindings: dict[str, tuple[int | None, str | None]],
) -> set[str]:
    if set(bindings) != run_ids:
        raise InvalidLogBatchError("日志批次包含不存在的 TaskRun")
    if any(assigned_worker != worker_id for assigned_worker, _ in bindings.values()):
        raise InvalidLogBatchError("日志批次 TaskRun 不属于声明 Worker")
    if any(not stored for _, stored in bindings.values()):
        raise InvalidLogBatchError("日志批次 TaskRun 尚未由 ownership claim 绑定")
    return {run_id for run_id, (_, stored) in bindings.items() if stored != lease_id}


async def _require_generation_backlog(
    worker_id: int,
    lease_id: str,
    run_ids: set[str],
    *,
    msg_id: str,
) -> None:
    rows = await TaskRunLeaseGeneration.filter(
        run_id__in=run_ids,
        worker_id=worker_id,
        lease_id=lease_id,
        log_valid_through_id__isnull=False,
    ).values_list("run_id", "log_valid_through_id")
    cutoffs = {str(run_id): str(cutoff) for run_id, cutoff in rows}
    if set(cutoffs) != run_ids:
        raise InvalidLogBatchError("日志批次 lease_id 不在 TaskRun 已确认代际历史中")
    if any(not stream_id_not_after(msg_id, cutoff) for cutoff in cutoffs.values()):
        raise InvalidLogBatchError("日志批次超过旧 Lease 的已确认 Stream cutoff")


def _field_bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    raise ValueError(f"log ingest Redis frame 字段类型非法: {type(value).__name__}")


__all__ = [
    "BoundedLogBatchCodec",
    "MAX_LOG_INGEST_FRAME_BYTES",
    "require_log_batch_integrity",
]
