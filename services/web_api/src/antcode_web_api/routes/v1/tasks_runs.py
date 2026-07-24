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
from antcode_core.common.security.auth import get_current_user
from antcode_core.domain.models import TaskRun
from antcode_core.domain.schemas.common import BaseResponse, PaginationResponse
from fastapi import Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from loguru import logger

from antcode_web_api.response import Messages
from antcode_web_api.response import (
    page as page_response,
)
from antcode_web_api.response import (
    success as success_response,
)
from antcode_web_api.routes.v1.task_cancel import (
    CANCEL_TERMINAL_CONFLICT_STATUSES,
    CANCELLABLE_TASK_STATUSES,
    is_unassigned_task_run,
    mark_task_run_cancelled,
    send_task_cancel,
    stop_unassigned_task_run,
)


async def _get_stoppable_execution(run_id: str, user_id: int) -> TaskRun:
    execution = await scheduler_service.get_execution_with_permission(run_id, user_id)
    if not execution:
        raise HTTPException(status_code=404, detail="执行记录不存在或无权访问")
    return execution


def _stop_task_response(run_id: str, *, remote_cancelled: bool) -> BaseResponse[dict]:
    return success_response(
        {"run_id": run_id, "status": "cancelled", "remote_cancelled": remote_cancelled},
        message="任务已停止",
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


async def stop_task_execution(run_id: str, current_user, *, tasks_module=None) -> BaseResponse[dict]:
    """停止任务执行

    T5: 对未被 dispatch 出去的执行（PENDING/QUEUED 且 worker_id 为空）使用 CAS
    UPDATE 直接置为 CANCELLED，避免 read-modify-write 之间的竞态：若 UPDATE
    实际命中 0 行，说明已被分发，改走 worker 控制平面。

    ``tasks_module`` 由主 tasks.py 传入 (sys.modules[__name__]), handler 内
    通过 ``tasks_module.<name>`` lookup 关键函数, 让测试 monkeypatch
    ``tasks.is_unassigned_task_run`` 等能生效。
    """
    tm = tasks_module
    get_stoppable = tm._get_stoppable_execution if tm else _get_stoppable_execution
    is_unassigned = tm.is_unassigned_task_run if tm else is_unassigned_task_run
    stop_unassigned = tm.stop_unassigned_task_run if tm else stop_unassigned_task_run
    try_send_stop = tm._try_send_stop_event_with_reason if tm else _try_send_stop_event_with_reason
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
    # P1-13：与新 /runs/{run_id}/cancel 语义对齐。已 dispatch 到 Worker 的 run
    # 必须先成功发出取消 control，才能把 TaskRun 标 CANCELLED——否则界面显示
    # 已取消、真实进程继续跑（Redis/Gateway 不可达时更明显）。
    cancelled, _send_error = await try_send_stop(execution, current_user.user_id)
    if execution.worker_id and not cancelled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="取消指令发送失败，请重试",
        )
    # P1-FN-02: CAS 未生效且 run 已进其它终态时按 409 返回真实状态，
    # 不对外谎报 cancelled。
    marked = await mark_cancelled(execution.run_id, current_user.user_id)
    if not marked:
        await raise_terminal_conflict(run_id, current_user.user_id)
    return stop_response(run_id, remote_cancelled=cancelled)


async def get_task_execution_logs(
    run_id: str,
    *,
    page: int,
    size: int,
    current_user,
) -> PaginationResponse[dict]:
    """获取任务执行日志（分页）

    T6 备注: 若 task_log_service 后续提供 list_entries(run_id, offset, limit)
    走 PG 的接口（由 Agent Y 提供），应直接替换为按需查询，无需读全量内容。
    当前实现按行解析后切片，对超大日志而言成本较高。
    """
    from antcode_core.application.services.logs.task_log_service import task_log_service

    execution = await scheduler_service.get_execution_with_permission(run_id, current_user.user_id)
    if not execution:
        raise HTTPException(status_code=404, detail="执行记录不存在或无权访问")

    logs = await task_log_service.get_execution_logs(execution.run_id)

    # 单次扫描组装条目，避免对超大文本反复 splitlines + strip
    items: list[dict] = []
    for log_type, payload_key in (("stdout", "output"), ("stderr", "error")):
        raw = logs.get(payload_key) or ""
        if not raw:
            continue
        for line in raw.splitlines():
            stripped = line.strip()
            if stripped:
                items.append({"type": log_type, "message": stripped})

    total = len(items)
    start = (page - 1) * size
    end = start + size
    return page_response(
        items=items[start:end],
        total=total,
        page=page,
        size=size,
        message=Messages.QUERY_SUCCESS,
    )


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
    async def _stop_task_execution(run_id: str, current_user=Depends(get_current_user)):
        """停止任务执行

        T5: 对未被 dispatch 出去的执行（PENDING/QUEUED 且 worker_id 为空）使用 CAS
        UPDATE 直接置为 CANCELLED，避免 read-modify-write 之间的竞态：若 UPDATE
        实际命中 0 行，说明已被分发，改走 worker 控制平面。
        """
        return await stop_task_execution(run_id, current_user, tasks_module=tasks_module)

    @router.get("/runs/{run_id}/logs", response_model=PaginationResponse[dict])
    async def _get_task_execution_logs(
        run_id: str,
        *,
        page: int = Query(1, ge=1),
        size: int = Query(200, ge=1, le=1000),
        current_user=Depends(get_current_user),
    ):
        """获取任务执行日志（分页）

        T6 备注: 若 task_log_service 后续提供 list_entries(run_id, offset, limit)
        走 PG 的接口（由 Agent Y 提供），应直接替换为按需查询，无需读全量内容。
        当前实现按行解析后切片，对超大日志而言成本较高。
        """
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
