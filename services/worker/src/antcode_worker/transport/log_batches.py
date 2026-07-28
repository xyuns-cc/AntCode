"""Transport-neutral ``LogBatch`` construction shared by Gateway and Direct.

单一构批路径（P1-DR-03）：Direct 与 Gateway 共用同一套 8 MiB 批 /
1 MiB 单行预算（``antcode_core.common.log_limits``），Worker 端超限
显式抛 ``ValueError``，不再出现"生产端 XADD 成功、Master 消费端拒收
进 DLQ"的契约不一致。

幂等（P1-GW-03）：每个批次落一个内容确定性的 ``batch_id``。同一进程内
重发（gateway StreamLogs 重试 / direct 重新 XADD）重建出的批次字节相同
→ ``batch_id`` 相同 → Master 侧 event_id（``batch_id:index``）相同 →
``task_logs.event_id`` 唯一索引去重，不会重复入库。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from antcode_contracts import data_pb2
from antcode_contracts.transcode import datetime_to_proto_timestamp, log_type_str_to_proto

# P1-GW-05: 哈希实现移入 core，与 Gateway/Master 服务端复核共用同一份。
from antcode_core.common.log_batch_hash import (
    BATCH_ID_PLACEHOLDER as _BATCH_ID_PLACEHOLDER,
)
from antcode_core.common.log_batch_hash import (
    deterministic_batch_id,
)
from antcode_core.common.log_limits import LogBatchLimits
from antcode_core.observability.tracing import get_current_trace, inject_trace, new_trace


def encode_log_entry(log: Any) -> data_pb2.LogEntry:
    """``LogMessage`` → ``data_pb2.LogEntry``（Gateway/Direct 唯一实现）。"""
    entry = data_pb2.LogEntry(
        run_id=log.run_id or "",
        log_type=log_type_str_to_proto(log.log_type),
        content=log.content or "",
        sequence=int(log.sequence or 0),
    )
    ts = datetime_to_proto_timestamp(log.timestamp or datetime.now())
    if ts is not None:
        entry.timestamp.CopyFrom(ts)
    return entry


def build_log_batches(
    logs: list[Any],
    *,
    worker_id: str,
    lease_id: str,
    limits: LogBatchLimits,
) -> list[data_pb2.LogBatch]:
    """Partition logs without exceeding the shared encoded byte budgets."""
    if not logs:
        return []
    traceparent = get_current_trace() or new_trace().traceparent
    batches: list[data_pb2.LogBatch] = []
    batch = _new_batch(worker_id, lease_id, traceparent)
    for index, log in enumerate(logs):
        entry = encode_log_entry(log)
        _validate_entry_content(entry, index, limits)
        batch.entries.append(entry)
        if batch.ByteSize() <= limits.max_batch_bytes:
            continue
        del batch.entries[-1]
        if not batch.entries:
            _raise_batch_overflow(index, batch, entry, limits=limits)
        batches.append(batch)
        batch = _new_batch(worker_id, lease_id, traceparent)
        batch.entries.append(entry)
        if batch.ByteSize() > limits.max_batch_bytes:
            _raise_batch_overflow(index, batch, entry, limits=limits)
    batches.append(batch)
    for built in batches:
        built.batch_id = deterministic_batch_id(worker_id, built.entries)
    return batches


async def iter_log_batches(batches: list[data_pb2.LogBatch]) -> AsyncIterator[data_pb2.LogBatch]:
    for batch in batches:
        yield batch


def is_invalid_argument_error(exc: Exception) -> bool:
    """Return whether a gRPC-shaped exception is a permanent input error."""
    import grpc

    code = getattr(exc, "code", None)
    return callable(code) and code() == grpc.StatusCode.INVALID_ARGUMENT


def _new_batch(worker_id: str, lease_id: str, traceparent: str) -> data_pb2.LogBatch:
    batch = data_pb2.LogBatch(
        worker_id=worker_id,
        lease_id=lease_id,
        batch_id=_BATCH_ID_PLACEHOLDER,
    )
    inject_trace(batch, traceparent=traceparent)
    return batch


def _validate_entry_content(entry: data_pb2.LogEntry, index: int, limits: LogBatchLimits) -> None:
    content_bytes = len(entry.content.encode("utf-8"))
    if content_bytes > limits.max_entry_content_bytes:
        raise ValueError(
            f"LogEntry content bytes 超限: index={index} bytes={content_bytes} > {limits.max_entry_content_bytes}"
        )


def _raise_batch_overflow(
    index: int,
    batch: data_pb2.LogBatch,
    entry: data_pb2.LogEntry,
    *,
    limits: LogBatchLimits,
) -> None:
    if not batch.entries:
        batch.entries.append(entry)
    raise ValueError(
        f"单条日志编码后无法放入 LogBatch: index={index} bytes={batch.ByteSize()} > {limits.max_batch_bytes}"
    )


__all__ = [
    "build_log_batches",
    "deterministic_batch_id",
    "encode_log_entry",
    "is_invalid_argument_error",
    "iter_log_batches",
]
