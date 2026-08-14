"""Gateway data 面入口守卫（P1-SEC-04 / receipt 归属）。"""

from __future__ import annotations

import grpc
from antcode_contracts import data_pb2
from antcode_core.application.services.workers.task_status_validation import (
    MAX_STATUS_ERROR_BYTES,
    MAX_STATUS_FRAME_BYTES,
    status_payload_error,
)
from antcode_core.infrastructure.redis import task_ready_stream

# 复审 P1-SEC-04: 单帧 TaskStatus 上限——status/result 是小元数据，50MiB
# gRPC 上限对它过大，受控 Worker 可用超大 result/error 正文灌满 Redis/PG。
# 大体积产物必须走 artifact 通道（那侧有 per-run 配额）。
_TIMESTAMP_SECONDS_UPPER = 253_402_300_799
_TIMESTAMP_NANOS_UPPER = 1_000_000_000
_PROGRESS_STATUSES = frozenset({data_pb2.STATUS_PENDING, data_pb2.STATUS_RUNNING})
_TERMINAL_STATUSES = frozenset(
    {
        data_pb2.STATUS_COMPLETED,
        data_pb2.STATUS_FAILED,
        data_pb2.STATUS_CANCELLED,
        data_pb2.STATUS_TIMEOUT,
    }
)
_VALID_STATUSES = _PROGRESS_STATUSES | _TERMINAL_STATUSES


async def require_bounded_status_frame(context: grpc.aio.ServicerContext, task_status) -> bool:
    frame_bytes = task_status.ByteSize()
    if frame_bytes <= MAX_STATUS_FRAME_BYTES:
        return await require_valid_status_frame(context, task_status)
    await context.abort(
        grpc.StatusCode.INVALID_ARGUMENT,
        f"status frame too large: {frame_bytes} > {MAX_STATUS_FRAME_BYTES}",
    )
    return False


async def require_valid_status_frame(context: grpc.aio.ServicerContext, task_status) -> bool:
    """Reject contradictory status/timing metadata before stream persistence."""
    error = _status_frame_error(task_status)
    if error is None:
        return True
    await context.abort(grpc.StatusCode.INVALID_ARGUMENT, error)
    return False


def _status_frame_error(task_status) -> str | None:
    if error := status_payload_error(task_status):
        return error
    status = int(task_status.status)
    if status not in _VALID_STATUSES:
        return "status 不是受支持的 TaskStatus 枚举"
    timing_error = _status_timing_error(task_status, status)
    return timing_error


def _status_timing_error(task_status, status: int) -> str | None:
    started = task_status.started_at if task_status.HasField("started_at") else None
    finished = task_status.finished_at if task_status.HasField("finished_at") else None
    if error := _timestamps_error(started, finished):
        return error
    if int(task_status.duration_ms) < 0:
        return "duration_ms 必须是非负数"
    if status in _PROGRESS_STATUSES:
        return _progress_timing_error(finished, int(task_status.duration_ms))
    return _terminal_timing_error(started, finished)


def _timestamps_error(started_at, finished_at) -> str | None:
    if error := _timestamp_error(started_at, "started_at"):
        return error
    return _timestamp_error(finished_at, "finished_at")


def _terminal_timing_error(started_at, finished_at) -> str | None:
    if finished_at is None:
        return "终态结果必须携带 finished_at"
    if started_at is not None and _timestamp_key(finished_at) < _timestamp_key(started_at):
        return "finished_at 不能早于 started_at"
    return None


def _timestamp_error(timestamp, field_name: str) -> str | None:
    if timestamp is None:
        return None
    seconds = int(timestamp.seconds)
    nanos = int(timestamp.nanos)
    if seconds == 0 and nanos == 0:
        return f"{field_name} 不能是未设置的零值时间"
    if not (0 <= seconds < _TIMESTAMP_SECONDS_UPPER):
        return f"{field_name}.seconds 超出支持范围"
    if not (0 <= nanos < _TIMESTAMP_NANOS_UPPER):
        return f"{field_name}.nanos 超出支持范围"
    return None


def _progress_timing_error(finished_at, duration_ms: int) -> str | None:
    if finished_at is not None:
        return "非终态结果不得携带 finished_at"
    if duration_ms != 0:
        return "非终态结果不得携带 duration_ms"
    return None


def _timestamp_key(timestamp) -> tuple[int, int]:
    return int(timestamp.seconds), int(timestamp.nanos)


async def require_owned_receipt(context: grpc.aio.ServicerContext, worker_id: str, receipt_id: str) -> bool:
    """receipt 必须存在且其 stream 归属已认证 worker。"""
    if not receipt_id:
        await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "receipt_id 缺失")
        return False
    receipt_stream = receipt_id.split("|", 1)[0]
    if receipt_stream != task_ready_stream(worker_id):
        await context.abort(
            grpc.StatusCode.PERMISSION_DENIED,
            "receipt stream does not belong to authenticated worker",
        )
        return False
    return True


__all__ = [
    "MAX_STATUS_FRAME_BYTES",
    "MAX_STATUS_ERROR_BYTES",
    "require_bounded_status_frame",
    "require_owned_receipt",
    "require_valid_status_frame",
]
