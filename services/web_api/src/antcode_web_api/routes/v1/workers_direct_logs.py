"""Trusted, generation-fenced log ingest for Direct Workers."""

from __future__ import annotations

import base64
import binascii
from typing import Any, Literal

from antcode_contracts import data_pb2
from antcode_core.application.services.workers.log_batch_validation import validate_log_batch
from antcode_core.application.services.workers.log_ingest_fence import (
    LogIngestFenceRejected,
    append_fenced_log_batch,
)
from antcode_core.application.services.workers.run_ownership_service import require_worker_owns_runs_for_lease
from antcode_core.common.log_limits import LogBatchLimits
from fastapi import HTTPException, status
from google.protobuf.message import DecodeError
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from antcode_web_api.response import BaseResponse, success
from antcode_web_api.routes.v1.workers_direct_control import _redis_client

_LOG_LIMITS = LogBatchLimits()
MAX_ENCODED_LOG_BATCH_CHARS = ((_LOG_LIMITS.max_batch_bytes + 2) // 3) * 4


class DirectLogBatchRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    operation: Literal["logs"]
    payload_base64: str = Field(min_length=1, max_length=MAX_ENCODED_LOG_BATCH_CHARS)


def _decode_log_batch(encoded: str) -> tuple[data_pb2.LogBatch, bytes]:
    try:
        payload = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("日志批次 base64 非法") from exc
    if len(payload) > _LOG_LIMITS.max_batch_bytes:
        raise ValueError("日志批次 protobuf bytes 超限")
    try:
        batch = data_pb2.LogBatch.FromString(payload)
    except DecodeError as exc:
        raise ValueError("日志批次 protobuf 非法") from exc
    validate_log_batch(batch, limits=_LOG_LIMITS)
    if not batch.entries:
        raise ValueError("日志批次 entries 不能为空")
    return batch, payload


async def ingest_direct_log_batch(worker: Any, request: DirectLogBatchRequest) -> BaseResponse:
    try:
        batch, payload = _decode_log_batch(request.payload_base64)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    worker_id = str(worker.public_id)
    if batch.worker_id != worker_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="日志批次 Worker 身份不匹配")
    run_ids = {entry.run_id for entry in batch.entries}
    try:
        await require_worker_owns_runs_for_lease(worker, run_ids, lease_id=batch.lease_id)
        message_id = await append_fenced_log_batch(
            await _redis_client(),
            payload,
            worker_id=worker_id,
            lease_id=batch.lease_id,
            run_ids=run_ids,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except LogIngestFenceRejected as exc:
        raise HTTPException(status_code=status.HTTP_412_PRECONDITION_FAILED, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Direct 日志写入失败: worker_id={} batch_id={}", worker_id, batch.batch_id)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="日志摄取不可用") from exc
    return success({"written": True, "message_id": message_id})


__all__ = ["DirectLogBatchRequest", "MAX_ENCODED_LOG_BATCH_CHARS", "ingest_direct_log_batch"]
