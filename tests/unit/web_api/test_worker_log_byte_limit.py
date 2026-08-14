import pytest
from antcode_contracts import data_pb2
from antcode_core.application.services.workers.log_batch_validation import validate_log_batch
from antcode_core.common.log_batch_hash import deterministic_batch_id
from antcode_core.common.log_limits import (
    DEFAULT_LOG_MAX_ENTRY_CONTENT_BYTES,
    MAX_SSE_LOG_MESSAGE_BYTES,
    LogBatchLimits,
)
from antcode_web_api.routes.v1.workers_report import WorkerTaskLogReportRequest
from antcode_web_api.streams.sse import build_log_line_message, format_sse_event
from pydantic import ValidationError


def test_worker_http_log_accepts_shared_utf8_byte_limit():
    request = WorkerTaskLogReportRequest(
        run_id="run-1",
        content="a" * DEFAULT_LOG_MAX_ENTRY_CONTENT_BYTES,
    )

    assert len(request.content.encode("utf-8")) == DEFAULT_LOG_MAX_ENTRY_CONTENT_BYTES


def test_worker_http_log_rejects_multibyte_content_over_shared_limit():
    content = "中" * (DEFAULT_LOG_MAX_ENTRY_CONTENT_BYTES // 3 + 1)

    with pytest.raises(ValidationError, match="UTF-8 字节数超限"):
        WorkerTaskLogReportRequest(run_id="run-1", content=content)


def test_direct_log_batch_enforces_four_byte_unicode_boundary():
    four_byte_code_point = chr(0x1F600)
    within_limit = four_byte_code_point * (DEFAULT_LOG_MAX_ENTRY_CONTENT_BYTES // 4)
    _validate_content(within_limit)

    with pytest.raises(ValueError, match="LogEntry content bytes 超限"):
        _validate_content(within_limit + four_byte_code_point)


def test_nul_content_worst_case_sse_frame_fits_reserved_budget():
    content = "\0" * DEFAULT_LOG_MAX_ENTRY_CONTENT_BYTES
    _validate_content(content)
    message = build_log_line_message(
        "r" * 64,
        log_type="l" * 16,
        content=content,
        timestamp="2026-07-17T00:00:00.000000+00:00",
        sequence=9_223_372_036_854_775_807,
        source="s" * 128,
        event_id="e" * 128,
        storage_id=9_223_372_036_854_775_807,
    )

    frame = format_sse_event("log_line", message, event_id="pg:9223372036854775807")

    assert len(frame) < MAX_SSE_LOG_MESSAGE_BYTES


def _validate_content(content: str) -> None:
    entry = data_pb2.LogEntry(run_id="run-1", content=content)
    batch = data_pb2.LogBatch(worker_id="worker-1", entries=[entry])
    batch.batch_id = deterministic_batch_id(batch.worker_id, batch.entries)
    validate_log_batch(batch, limits=LogBatchLimits())
