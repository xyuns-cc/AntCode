"""Worker report endpoints (log / heartbeat / status).

P2 拆分自 workers.py: 4 个 Worker 上报接口 (无用户认证, 仅 Worker mTLS +
签名 + API Key)。由主 workers.py 顶层 import 触发注册, 保持相同 router
实例与 URL 前缀。

保留的对外契约 (被单元测试直接引用):
- MAX_LOG_LINE_CHARS / MAX_LOG_BATCH_ENTRIES 常量
- WorkerTaskLogReportRequest / WorkerTaskLogsBatchReportRequest /
  WorkerTaskHeartbeatReportRequest / WorkerTaskStatusReportRequest 4 个 schema
- report_task_log / report_task_logs_batch / report_execution_heartbeat /
  report_task_status 4 个 handler
- _WorkerReportBaseModel base

workers.py 用 `from .workers_report import *` re-export 保持向后兼容。
"""

from __future__ import annotations

import asyncio

from fastapi import Body, Depends, HTTPException, status
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from antcode_web_api.response import BaseResponse, success

# router 由 workers.py 导入并注入, 避免循环 import 又保持同一 router 实例。
# 拆分时的实践: 让被拆分模块只导入必要的 Depends 目标, router 引用通过
# _register_report_routes(router, verify_worker_credential_headers) 注入。

MAX_LOG_LINE_CHARS = 1_048_576
MAX_LOG_BATCH_ENTRIES = 1_000


class _WorkerReportBaseModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class WorkerTaskLogReportRequest(_WorkerReportBaseModel):
    run_id: str = Field(..., min_length=1, description="任务运行 ID")
    log_type: str = Field(default="stdout", description="日志类型")
    content: str = Field(..., min_length=1, max_length=MAX_LOG_LINE_CHARS, description="日志内容")


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


# 批量日志并发上限, 防止单 Worker 单批耗尽 async 事件循环资源
_APPEND_LOGS_CONCURRENCY = 16


async def report_task_log(
    request: WorkerTaskLogReportRequest,
    auth_context: dict,
):
    """任务日志上报（签名 + Worker 标识 + API Key）"""
    from antcode_core.application.services.logs.postgres_log_service import TaskRunGoneError
    from antcode_core.application.services.workers.distributed_log_service import distributed_log_service
    from antcode_core.application.services.workers.run_ownership_service import (
        require_worker_owns_run,
    )

    await require_worker_owns_run(auth_context["worker"], request.run_id)

    try:
        await distributed_log_service.append_log(
            request.run_id,
            request.log_type,
            request.content,
        )
    except TaskRunGoneError as exc:
        # P1-DB-03: run 已被删除(与删除路径的 advisory lock 串行化后在锁内
        # 校验命中), 明确 409 拒绝而不是 5xx 引导 Worker 无限重试。
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="任务执行已删除，日志被拒绝") from exc

    return success({"received": True})


async def report_task_logs_batch(
    request: WorkerTaskLogsBatchReportRequest,
    auth_context: dict,
):
    """批量任务日志上报（签名 + Worker 标识 + API Key）"""
    from antcode_core.application.services.logs.postgres_log_service import TaskRunGoneError
    from antcode_core.application.services.workers.distributed_log_service import distributed_log_service
    from antcode_core.application.services.workers.run_ownership_service import (
        require_worker_owns_runs,
    )

    logs = request.logs
    await require_worker_owns_runs(auth_context["worker"], {item.run_id for item in logs})

    grouped_logs: dict[tuple[str, str], list[str]] = {}
    for item in logs:
        key = (item.run_id, item.log_type)
        grouped_logs.setdefault(key, []).append(item.content)

    semaphore = asyncio.Semaphore(_APPEND_LOGS_CONCURRENCY)

    async def _append_group(run_id: str, log_type: str, contents: list[str]) -> int:
        async with semaphore:
            await distributed_log_service.append_logs(run_id, log_type, contents)
        return len(contents)

    group_keys = list(grouped_logs.keys())
    results = await asyncio.gather(
        *(_append_group(run_id, log_type, contents) for (run_id, log_type), contents in grouped_logs.items()),
        return_exceptions=True,
    )
    failed_pairs = [(group_keys[i], result) for i, result in enumerate(results) if isinstance(result, Exception)]
    if failed_pairs:
        logger.error("批量日志写入失败: failed_groups={} total_groups={}", len(failed_pairs), len(results))
        # P1-DB-03: 全部失败均为"run 已删除"时返 409(永久拒绝, 无重试意义)。
        # P1-round6 5.2: 部分失败改返 207 Multi-Status 携带 failed_groups,
        # 让 Worker 只重试失败组; 全体 503 会让已成功组通过 sequence 递增
        # 复制入库。
        failed_exceptions = [exc for _, exc in failed_pairs]
        if all(isinstance(exc, TaskRunGoneError) for exc in failed_exceptions):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="任务执行已删除，日志被拒绝")
        received_count = sum(result for result in results if isinstance(result, int))
        failed_groups = [{"run_id": rid, "log_type": lt, "error": str(exc)} for (rid, lt), exc in failed_pairs]
        raise HTTPException(
            status_code=status.HTTP_207_MULTI_STATUS,
            detail={
                "message": "批量日志部分失败, 请只重试失败组",
                "received": received_count,
                "total": len(logs),
                "failed_groups": failed_groups,
            },
        )
    received_count = sum(result for result in results if isinstance(result, int))

    return success({"received": received_count, "total": len(logs)})


async def report_execution_heartbeat(
    request: WorkerTaskHeartbeatReportRequest,
    auth_context: dict,
):
    """任务执行心跳上报"""
    from antcode_core.application.services.scheduler.task_persistence import task_persistence_service
    from antcode_core.application.services.workers.run_ownership_service import (
        require_worker_owns_run,
    )

    await require_worker_owns_run(auth_context["worker"], request.run_id)

    success_flag = await task_persistence_service.update_heartbeat(request.run_id)
    return success({"updated": success_flag})


async def report_task_status(
    request: WorkerTaskStatusReportRequest,
    auth_context: dict,
):
    """任务状态上报（签名 + Worker 标识 + API Key）"""
    from antcode_core.application.services.workers.distributed_log_service import distributed_log_service
    from antcode_core.application.services.workers.run_ownership_service import (
        require_worker_owns_run,
    )

    await require_worker_owns_run(auth_context["worker"], request.run_id)

    await distributed_log_service.update_task_status(
        request.run_id,
        request.status,
        exit_code=request.exit_code,
        error_message=request.error_message,
    )

    return success({"updated": True})


def register_report_routes(router, verify_worker_credential_headers) -> None:
    """将 4 个 Worker 上报路由注册到 workers.router (由 workers.py 顶层调用)。

    传入 verify_worker_credential_headers 依赖, 避免循环 import。
    """

    @router.post(
        "/report-log",
        response_model=BaseResponse[dict],
        summary="上报任务日志",
        description="Worker 实时上报任务执行日志",
    )
    async def _report_task_log(
        request: WorkerTaskLogReportRequest = Body(...),
        auth_context: dict = Depends(verify_worker_credential_headers),
    ):
        return await report_task_log(request, auth_context)

    @router.post(
        "/report-logs-batch",
        response_model=BaseResponse[dict],
        summary="批量上报任务日志",
        description="Worker 批量上报任务执行日志",
    )
    async def _report_task_logs_batch(
        request: WorkerTaskLogsBatchReportRequest = Body(...),
        auth_context: dict = Depends(verify_worker_credential_headers),
    ):
        return await report_task_logs_batch(request, auth_context)

    @router.post(
        "/report-heartbeat",
        response_model=BaseResponse[dict],
        summary="上报任务执行心跳",
        description="Worker 上报任务执行心跳，用于检测任务中断",
    )
    async def _report_execution_heartbeat(
        request: WorkerTaskHeartbeatReportRequest = Body(...),
        auth_context: dict = Depends(verify_worker_credential_headers),
    ):
        return await report_execution_heartbeat(request, auth_context)

    @router.post(
        "/report-task",
        response_model=BaseResponse[dict],
        summary="上报任务状态",
        description="Worker 上报任务执行状态",
    )
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
