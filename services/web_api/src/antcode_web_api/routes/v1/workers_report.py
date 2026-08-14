"""Explicitly retired legacy HTTP Worker report endpoints.

Current Workers report logs, heartbeats, and results through generation-fenced
Direct or Gateway transports. The former HMAC-only protocol cannot distinguish
two Lease generations of the same Worker identity, so it must not mutate state.
"""

from __future__ import annotations

from antcode_core.common.log_limits import DEFAULT_LOG_MAX_ENTRY_CONTENT_BYTES
from fastapi import Body, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from antcode_web_api.response import BaseResponse

MAX_LOG_LINE_CHARS = 1_048_576
MAX_LOG_BATCH_ENTRIES = 1_000
_LEGACY_REPORT_DETAIL = "旧 Worker HTTP 上报协议已下线，请使用 Direct Lease 或 Gateway mTLS 传输"


class _WorkerReportBaseModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class WorkerTaskLogReportRequest(_WorkerReportBaseModel):
    run_id: str = Field(..., min_length=1, description="任务运行 ID")
    log_type: str = Field(default="stdout", description="日志类型")
    content: str = Field(..., min_length=1, max_length=MAX_LOG_LINE_CHARS, description="日志内容")

    @field_validator("content")
    @classmethod
    def validate_content_bytes(cls, value: str) -> str:
        content_bytes = len(value.encode("utf-8"))
        if content_bytes > DEFAULT_LOG_MAX_ENTRY_CONTENT_BYTES:
            raise ValueError(f"日志内容 UTF-8 字节数超限: {content_bytes} > {DEFAULT_LOG_MAX_ENTRY_CONTENT_BYTES}")
        return value


class WorkerTaskLogsBatchReportRequest(_WorkerReportBaseModel):
    logs: list[WorkerTaskLogReportRequest] = Field(
        ...,
        min_length=1,
        max_length=MAX_LOG_BATCH_ENTRIES,
        description="批量日志条目",
    )


class WorkerTaskHeartbeatReportRequest(_WorkerReportBaseModel):
    run_id: str = Field(..., min_length=1, description="任务运行 ID")


class WorkerTaskStatusReportRequest(_WorkerReportBaseModel):
    run_id: str = Field(..., min_length=1, description="任务运行 ID")
    status: str = Field(..., min_length=1, description="任务状态")
    exit_code: int | None = Field(default=None, description="任务退出码")
    error_message: str | None = Field(default=None, description="错误信息")


def _raise_legacy_report_gone() -> None:
    raise HTTPException(status_code=status.HTTP_410_GONE, detail=_LEGACY_REPORT_DETAIL)


async def report_task_log(request: WorkerTaskLogReportRequest, auth_context: dict) -> None:
    _ = request, auth_context
    _raise_legacy_report_gone()


async def report_task_logs_batch(request: WorkerTaskLogsBatchReportRequest, auth_context: dict) -> None:
    _ = request, auth_context
    _raise_legacy_report_gone()


async def report_execution_heartbeat(request: WorkerTaskHeartbeatReportRequest, auth_context: dict) -> None:
    _ = request, auth_context
    _raise_legacy_report_gone()


async def report_task_status(request: WorkerTaskStatusReportRequest, auth_context: dict) -> None:
    _ = request, auth_context
    _raise_legacy_report_gone()


def register_report_routes(router, verify_worker_credential_headers) -> None:
    """Keep 410 shims so old Workers fail explicitly during upgrade."""

    @router.post("/report-log", response_model=BaseResponse[dict], summary="旧 Worker 日志上报（已下线）")
    async def _report_task_log(
        request: WorkerTaskLogReportRequest = Body(...),
        auth_context: dict = Depends(verify_worker_credential_headers),
    ):
        return await report_task_log(request, auth_context)

    @router.post("/report-logs-batch", response_model=BaseResponse[dict], summary="旧 Worker 批量日志上报（已下线）")
    async def _report_task_logs_batch(
        request: WorkerTaskLogsBatchReportRequest = Body(...),
        auth_context: dict = Depends(verify_worker_credential_headers),
    ):
        return await report_task_logs_batch(request, auth_context)

    @router.post("/report-heartbeat", response_model=BaseResponse[dict], summary="旧任务心跳上报（已下线）")
    async def _report_execution_heartbeat(
        request: WorkerTaskHeartbeatReportRequest = Body(...),
        auth_context: dict = Depends(verify_worker_credential_headers),
    ):
        return await report_execution_heartbeat(request, auth_context)

    @router.post("/report-task", response_model=BaseResponse[dict], summary="旧任务状态上报（已下线）")
    async def _report_task_status(
        request: WorkerTaskStatusReportRequest = Body(...),
        auth_context: dict = Depends(verify_worker_credential_headers),
    ):
        return await report_task_status(request, auth_context)


__all__ = [
    "MAX_LOG_BATCH_ENTRIES",
    "MAX_LOG_LINE_CHARS",
    "WorkerTaskHeartbeatReportRequest",
    "WorkerTaskLogReportRequest",
    "WorkerTaskLogsBatchReportRequest",
    "WorkerTaskStatusReportRequest",
    "_WorkerReportBaseModel",
    "register_report_routes",
    "report_execution_heartbeat",
    "report_task_log",
    "report_task_logs_batch",
    "report_task_status",
]
