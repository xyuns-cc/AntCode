"""Shared validation for Worker log batches at trusted ingest boundaries."""

from __future__ import annotations

from typing import Any

from antcode_core.common.log_batch_hash import verify_batch_id
from antcode_core.common.log_limits import LogBatchLimits


def validate_log_batch(batch: Any, *, limits: LogBatchLimits) -> None:
    batch_bytes = batch.ByteSize()
    if batch_bytes > limits.max_batch_bytes:
        raise ValueError(f"LogBatch protobuf bytes 超限: {batch_bytes} > {limits.max_batch_bytes}")
    _validate_entries(batch.entries, limits=limits)
    if not verify_batch_id(batch.worker_id, batch.entries, batch.batch_id or ""):
        raise ValueError("LogBatch batch_id 非法（要求 64 位 sha256 hex 且与内容哈希一致）")


def _validate_entries(entries: Any, *, limits: LogBatchLimits) -> None:
    for index, entry in enumerate(entries):
        if not entry.run_id or entry.run_id != entry.run_id.strip():
            raise ValueError(f"LogEntry run_id 非法: index={index}")
        content_bytes = len(entry.content.encode("utf-8"))
        if content_bytes > limits.max_entry_content_bytes:
            raise ValueError(
                f"LogEntry content bytes 超限: index={index} bytes={content_bytes} > {limits.max_entry_content_bytes}"
            )


__all__ = ["validate_log_batch"]
