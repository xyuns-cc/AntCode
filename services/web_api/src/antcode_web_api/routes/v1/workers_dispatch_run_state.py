"""手动派发产生的 TaskRun 状态收敛，以及分发失败在 HTTP 边界的翻译。

分发链路上有三个必须落库的状态跃迁：分发前校验/异常失败的补偿收敛、
分发被 Worker 拒绝的终态收敛、分发成功后的 CAS 回写。它们共享同一条
"绝不 clobber 已被推进的 run"的约束，集中在本模块维护。

失败翻译也放这里：单任务与批量两条路由要用同一张 error_code → HTTP 状态的表，
拆成两份迟早会漂成"同一种失败两个状态码"。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, NoReturn

from antcode_core.application.services.workers.dispatch_error_codes import CAPACITY_ERROR_CODES
from antcode_core.common.error_messages import normalize_persisted_error_message
from antcode_core.domain.models import DispatchStatus, TaskRun, TaskStatus, Worker
from fastapi import HTTPException, status
from loguru import logger

_DEFAULT_DISPATCH_ERROR = "任务分发失败"
# 容量类失败对调用方是"稍后重试"，不是"我们坏了"。两边都只回静态文案：
# result.error 里可能带 DSN / 内部主机名，503 分支同样不许把它带出去。
_CAPACITY_DETAIL = "暂无可用 Worker，请稍后重试"


def dispatch_failure_response(error_code: str | None, *, detail: str) -> HTTPException:
    """按结构化码翻状态码。**只看 error_code**，绝不匹配 ``error`` 文案。"""
    if error_code in CAPACITY_ERROR_CODES:
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_CAPACITY_DETAIL)
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail)


async def finalize_failed_dispatch_run(run_id: str, reason: str) -> None:
    """P1-FN-07 补偿:仍处于 pending 的直接分发 run 收敛为 FAILED。"""
    try:
        await TaskRun.filter(run_id=run_id, status=TaskStatus.PENDING).update(
            status=TaskStatus.FAILED,
            dispatch_status=DispatchStatus.FAILED,
            end_time=datetime.now(UTC),
            error_message=normalize_persisted_error_message(reason),
        )
    except Exception:
        logger.exception("直接分发失败补偿未完成(交由 reconcile 兜底): run_id={}", run_id)


async def fail_task_dispatch(task_run: TaskRun, run_id: str, error: str | None, *, error_code: str | None) -> NoReturn:
    """分发没成：run 落终态，状态码由 ``error_code`` 决定（容量类 503，其余 500）。"""
    task_run.status = TaskStatus.FAILED
    task_run.dispatch_status = DispatchStatus.FAILED
    task_run.error_message = (
        normalize_persisted_error_message(error or _DEFAULT_DISPATCH_ERROR) or _DEFAULT_DISPATCH_ERROR
    )
    await task_run.save()
    logger.error("任务分发失败: run_id={} error_code={} error={}", run_id, error_code, error)
    raise dispatch_failure_response(error_code, detail=_DEFAULT_DISPATCH_ERROR)


async def mark_run_dispatched(run_id: str, worker_public_id: str | None) -> None:
    """复审 F5-B: 定向 CAS 更新, 避免整行 save() clobber 已终态的 run。"""
    dispatch_updates: dict[str, Any] = {"dispatch_status": DispatchStatus.DISPATCHED}
    worker_row_id = await _resolve_worker_row_id(worker_public_id)
    if worker_row_id is not None:
        dispatch_updates["worker_id"] = worker_row_id
    updated = await TaskRun.filter(
        run_id=run_id,
        dispatch_status=DispatchStatus.PENDING,
    ).update(**dispatch_updates)
    if not updated:
        logger.warning("直接分发状态回写被跳过(run 已被推进/取消): run_id={}", run_id)


async def _resolve_worker_row_id(worker_public_id: str | None) -> int | None:
    if not worker_public_id:
        return None
    worker = await Worker.filter(public_id=worker_public_id).only("id").first()
    return worker.id if worker else None


__all__ = [
    "dispatch_failure_response",
    "fail_task_dispatch",
    "finalize_failed_dispatch_run",
    "mark_run_dispatched",
]
