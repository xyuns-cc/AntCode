"""任务运行(TaskRun) 相关接口: 停止 / 日志分页 / 日志下载。

P2 拆分自 tasks.py: 3 个 run 级 handler:
- POST /tasks/runs/{run_id}/stop (stop_task_execution)
- GET /tasks/runs/{run_id}/logs (get_task_execution_logs)
- GET /tasks/runs/{run_id}/logs/download (download_task_execution_logs)

契约 (URL / DI / 返回) 与旧实现一致。stop_task_execution 依赖
antcode_web_api.routes.v1.task_cancel 模块中的多个工具函数, 保持导入路径。
"""

from __future__ import annotations

import io
import json

from antcode_core.application.services.scheduler.scheduler_service import scheduler_service
from antcode_core.common.log_limits import MEBIBYTE
from antcode_core.common.security.auth import get_current_user
from antcode_core.domain.models import TaskRun
from antcode_core.domain.schemas.common import BaseResponse, PaginationResponse
from fastapi import Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from loguru import logger

from antcode_web_api.response import Messages
from antcode_web_api.response import (
    page as page_response,
)
from antcode_web_api.response import (
    success as success_response,
)
from antcode_web_api.routes.v1.mutation_audit import audit_task_stopped
from antcode_web_api.routes.v1.task_cancel import (
    CANCEL_TERMINAL_CONFLICT_STATUSES,
    CANCELLABLE_TASK_STATUSES,
    is_unassigned_task_run,
    mark_task_run_cancelled,
    record_task_cancel_request,
    send_task_cancel,
    stop_unassigned_task_run,
)

MAX_LOG_PAGE_ENTRIES = 32
MAX_LOG_PAGE_BYTES = 8 * MEBIBYTE


async def _get_stoppable_execution(run_id: str, user_id: int) -> TaskRun:
    execution = await scheduler_service.get_execution_with_permission(run_id, user_id)
    if not execution:
        raise HTTPException(status_code=404, detail="执行记录不存在或无权访问")
    return execution


def _stop_task_response(run_id: str, *, remote_cancelled: bool) -> BaseResponse[dict]:
    result_status = "cancel_requested" if remote_cancelled else "cancelled"
    message = "取消请求已受理" if remote_cancelled else "任务已停止"
    return success_response(
        {"run_id": run_id, "status": result_status, "remote_cancelled": remote_cancelled},
        message=message,
    )


async def _try_send_stop_event_with_reason(
    execution: TaskRun,
    user_id: int,
) -> tuple[bool, str | None]:
    """P1-13：返回 (发送成功, 错误消息)，让 stop 端点能像 cancel 一样区分故障。"""
    if not execution.worker_id:
        return False, None
    try:
        ok = await send_task_cancel(execution, user_id)
        return bool(ok), None
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"发送取消指令失败: {exc}")
        return False, str(exc)


async def _raise_if_stop_terminal_conflict(run_id: str, user_id: int) -> None:
    final = await _get_stoppable_execution(run_id, user_id)
    final_status = final.status.value if final.status else "unknown"
    if final_status in CANCEL_TERMINAL_CONFLICT_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"任务已进入终态({final_status})，取消未生效",
        )


async def stop_task_execution(run_id: str, current_user, *, http_request, tasks_module=None) -> BaseResponse[dict]:
    """停止任务执行；只有真的受理了停止才留审计。

    停止是人为干预正在跑的东西，必须能追溯到人。审计放在受理之后：中途抛
    400/409/503 表示没停成，不该在审计里留下"已停止"。
    """
    response = await _perform_task_stop(run_id, current_user, tasks_module=tasks_module)
    await audit_task_stopped(http_request, current_user, run_id=run_id)
    return response


async def _perform_task_stop(run_id: str, current_user, *, tasks_module=None) -> BaseResponse[dict]:
    """T5: 未派发的执行（PENDING/QUEUED 且 worker_id 为空）用 CAS UPDATE 直接置
    CANCELLED，避免 read-modify-write 竞态；命中 0 行说明已分发，改走 worker
    控制平面。``tasks_module`` 由主 tasks.py 传入，handler 内经
    ``tasks_module.<name>`` lookup 让测试 monkeypatch 生效。
    """
    tm = tasks_module
    get_stoppable = tm._get_stoppable_execution if tm else _get_stoppable_execution
    is_unassigned = tm.is_unassigned_task_run if tm else is_unassigned_task_run
    stop_unassigned = tm.stop_unassigned_task_run if tm else stop_unassigned_task_run
    try_send_stop = tm._try_send_stop_event_with_reason if tm else _try_send_stop_event_with_reason
    record_cancel = tm.record_task_cancel_request if tm else record_task_cancel_request
    mark_cancelled = tm.mark_task_run_cancelled if tm else mark_task_run_cancelled
    raise_terminal_conflict = tm._raise_if_stop_terminal_conflict if tm else _raise_if_stop_terminal_conflict
    stop_response = tm._stop_task_response if tm else _stop_task_response

    execution = await get_stoppable(run_id, current_user.user_id)
    if execution.status not in CANCELLABLE_TASK_STATUSES:
        raise HTTPException(status_code=400, detail="任务当前状态无法停止")
    if is_unassigned(execution):
        if await stop_unassigned(execution, current_user.user_id):
            return stop_response(run_id, remote_cancelled=False)
        execution = await get_stoppable(run_id, current_user.user_id)
    # 已分配 run 只确认取消 control 已可靠投递，终态由 Worker 的真实结果落库。
    # API 若提前写 CANCELLED，调用方可立即删除 TaskRun，导致 Worker 结算被拒。
    if execution.worker_id:
        if not await record_cancel(execution.run_id, current_user.user_id):
            await raise_terminal_conflict(run_id, current_user.user_id)
            return stop_response(run_id, remote_cancelled=False)
        cancelled, _send_error = await try_send_stop(execution, current_user.user_id)
        if not cancelled:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="取消指令发送失败，请重试",
            )
        return stop_response(run_id, remote_cancelled=True)
    # P1-FN-02: CAS 未生效且 run 已进其它终态时按 409 返回真实状态，
    # 不对外谎报 cancelled。
    marked = await mark_cancelled(execution.run_id, current_user.user_id)
    if not marked:
        await raise_terminal_conflict(run_id, current_user.user_id)
    return stop_response(run_id, remote_cancelled=False)


async def get_task_execution_logs(
    run_id: str,
    *,
    page: int,
    size: int,
    current_user,
) -> PaginationResponse[dict]:
    """获取任务执行日志（数据库真分页）。"""
    from antcode_core.domain.models import TaskLog

    execution = await scheduler_service.get_execution_with_permission(run_id, current_user.user_id)
    if not execution:
        raise HTTPException(status_code=404, detail="执行记录不存在或无权访问")

    query = TaskLog.filter(run_id=execution.run_id)
    total = await query.count()
    rows = await query.order_by("id").offset((page - 1) * size).limit(size)
    items = [{"type": row.log_type, "message": row.content} for row in rows]
    response = page_response(
        items=items,
        total=total,
        page=page,
        size=size,
        message=Messages.QUERY_SUCCESS,
    )
    if len(response.model_dump_json().encode("utf-8")) > MAX_LOG_PAGE_BYTES:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="日志分页响应超过 8 MiB 字节上限")
    return response


async def download_task_execution_logs(
    run_id: str,
    format: str,
    current_user,
):
    """下载任务执行日志"""
    from antcode_core.application.services.logs.task_log_service import task_log_service

    execution = await scheduler_service.get_execution_with_permission(run_id, current_user.user_id)
    if not execution:
        raise HTTPException(status_code=404, detail="执行记录不存在或无权访问")

    logs = await task_log_service.get_execution_logs(execution.run_id)
    if format == "json":
        content = json.dumps(logs, ensure_ascii=False, indent=2)
        media_type = "application/json"
        filename = f"run_{run_id}.json"
    else:
        content = "\n".join(
            [
                "=== STDOUT ===",
                logs.get("output", ""),
                "",
                "=== STDERR ===",
                logs.get("error", ""),
            ]
        )
        media_type = "text/plain"
        filename = f"run_{run_id}.txt"

    buffer = io.BytesIO(content.encode("utf-8"))
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(buffer, media_type=media_type, headers=headers)


def register_runs_routes(router, tasks_module) -> None:
    """``tasks_module`` 是主 tasks 模块 (import 传入), stop handler 通过
    ``tasks_module.<name>`` lookup 关键函数, 保证 monkeypatch 生效。
    """

    @router.post("/runs/{run_id}/stop", response_model=BaseResponse[dict])
    async def _stop_task_execution(run_id: str, current_user=Depends(get_current_user), *, http_request: Request):
        """停止任务执行

        T5: 对未被 dispatch 出去的执行（PENDING/QUEUED 且 worker_id 为空）使用 CAS
        UPDATE 直接置为 CANCELLED，避免 read-modify-write 之间的竞态：若 UPDATE
        实际命中 0 行，说明已被分发，改走 worker 控制平面。
        """
        return await stop_task_execution(
            run_id,
            current_user,
            http_request=http_request,
            tasks_module=tasks_module,
        )

    @router.get("/runs/{run_id}/logs", response_model=PaginationResponse[dict])
    async def _get_task_execution_logs(
        run_id: str,
        *,
        page: int = Query(1, ge=1),
        size: int = Query(MAX_LOG_PAGE_ENTRIES, ge=1, le=MAX_LOG_PAGE_ENTRIES),
        current_user=Depends(get_current_user),
    ):
        """获取任务执行日志（数据库真分页）。"""
        return await get_task_execution_logs(run_id, page=page, size=size, current_user=current_user)

    @router.get("/runs/{run_id}/logs/download", response_model=None)
    async def _download_task_execution_logs(
        run_id: str,
        format: str = Query("txt", pattern="^(txt|json)$"),
        current_user=Depends(get_current_user),
    ):
        """下载任务执行日志"""
        return await download_task_execution_logs(run_id, format, current_user)


__all__ = [
    "download_task_execution_logs",
    "get_task_execution_logs",
    "register_runs_routes",
    "stop_task_execution",
]
