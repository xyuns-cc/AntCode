"""State-machine based repairs used by the Master reconcile loop."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from antcode_core.application.services.scheduler.execution_status_service import (
    execution_status_service,
)
from antcode_core.domain.models import TaskRun
from antcode_core.domain.models.enums import DispatchStatus, RuntimeStatus, TaskStatus
from loguru import logger

ZOMBIE_TASK_AGE_HOURS = 24
ZOMBIE_ERROR_MESSAGE = "任务长时间未调度，已清理"
# 复审 F3-A: run 创建事务提交后、首次 DISPATCHING 写入前 Master 崩溃，
# 留下 status=QUEUED + dispatch=PENDING 的孤儿。正常流该组合只存在数秒，
# 15 分钟阈值远离误杀。
STUCK_QUEUED_AGE_SECONDS = 900
STUCK_QUEUED_ERROR_MESSAGE = "run 创建后未进入派发（Master 中断），已收敛失败"
# 复审 F2-A: TaskRun 状态更新与 Task 状态同步是两个事务，中间崩溃会让
# Task 永久卡在 busy 状态阻断后续调度。
STALE_TASK_STATUS_AGE_SECONDS = 900


async def repair_inconsistent_states() -> None:
    """Converge legacy RUNNING rows with an end time through runtime CAS."""
    try:
        success_runs = await _load_ended_runs(error_is_null=True)
        failed_runs = await _load_ended_runs(error_is_null=False)
        total = len(success_runs) + len(failed_runs)
        if not total:
            return
        logger.warning(f"发现 {total} 个状态不一致任务")
        success_count = await _repair_runtime_states(success_runs, RuntimeStatus.SUCCESS)
        failed_count = await _repair_runtime_states(failed_runs, RuntimeStatus.FAILED)
        logger.info(f"修复 SUCCESS={success_count} FAILED={failed_count}")
    except Exception:
        logger.exception("检测状态不一致失败")


async def _load_ended_runs(*, error_is_null: bool) -> list[Any]:
    query = TaskRun.filter(
        status=TaskStatus.RUNNING,
        end_time__isnull=False,
        error_message__isnull=error_is_null,
    )
    fields = ("run_id", "end_time") if error_is_null else ("run_id", "end_time", "error_message")
    return await query.only(*fields).all()


async def _repair_runtime_states(runs: list[Any], target_status: RuntimeStatus) -> int:
    repaired = 0
    for run in runs:
        updated = await execution_status_service.update_runtime_status(
            run_id=run.run_id,
            status=target_status,
            status_at=run.end_time,
            error_message=getattr(run, "error_message", None),
        )
        repaired += int(updated)
    return repaired


async def cleanup_zombie_tasks() -> None:
    """Fail stale pending runs through dispatch CAS instead of a bare update."""
    try:
        now = datetime.now(UTC)
        threshold = now - timedelta(hours=ZOMBIE_TASK_AGE_HOURS)
        runs = (
            await TaskRun.filter(
                status=TaskStatus.PENDING,
                created_at__lt=threshold,
            )
            .only("run_id")
            .all()
        )
        if not runs:
            return
        logger.warning(f"发现 {len(runs)} 个僵尸任务")
        updated = await _fail_zombie_runs(runs, now)
        logger.info(f"已清理 {updated}/{len(runs)} 个僵尸任务")
    except Exception:
        logger.exception("清理僵尸任务失败")


async def _fail_zombie_runs(runs: list[Any], status_at: datetime) -> int:
    updated = 0
    for run in runs:
        changed = await execution_status_service.update_dispatch_status(
            run_id=run.run_id,
            status=DispatchStatus.FAILED,
            status_at=status_at,
            error_message=ZOMBIE_ERROR_MESSAGE,
        )
        updated += int(changed)
    return updated


async def repair_stuck_queued_runs() -> None:
    """复审 F3-A: 收敛"已创建未派发"的孤儿 run（QUEUED + dispatch PENDING）。

    此前无任何 reaper 覆盖该组合（zombie 只扫 PENDING、no-ack 只扫
    DISPATCHING/DISPATCHED、超时只扫 RUNNING）：run 永久悬挂、Task 卡
    QUEUED、丢失的重试静默消失。经 dispatch CAS 收敛 FAILED，Task 状态
    由 update_dispatch_status 内部同步。
    """
    try:
        threshold = datetime.now(UTC) - timedelta(seconds=STUCK_QUEUED_AGE_SECONDS)
        runs = (
            await TaskRun.filter(
                status=TaskStatus.QUEUED,
                dispatch_status=DispatchStatus.PENDING,
                created_at__lt=threshold,
            )
            .only("run_id")
            .all()
        )
        if not runs:
            return
        logger.warning(f"发现 {len(runs)} 个创建后未派发的孤儿 run")
        now = datetime.now(UTC)
        repaired = 0
        for run in runs:
            changed = await execution_status_service.update_dispatch_status(
                run_id=run.run_id,
                status=DispatchStatus.FAILED,
                status_at=now,
                error_message=STUCK_QUEUED_ERROR_MESSAGE,
            )
            repaired += int(changed)
        logger.info(f"孤儿 QUEUED run 收敛 {repaired}/{len(runs)}")
    except Exception:
        logger.exception("孤儿 QUEUED run 收敛失败")


async def repair_stale_task_status() -> None:
    """复审 F2-A: 修复 Task 卡 busy 状态（最新 run 已终态但 Task 未同步）。

    TaskRun 状态 CAS 与 ``_sync_task_status`` 分属两个事务，中间崩溃后
    Task 永久停在 QUEUED/DISPATCHING/RUNNING，busy 检查阻断后续调度。
    这里按"最新 run 已终态且足够旧"把 Task 状态收敛到该 run 的终态。
    """
    from antcode_core.domain.models.task import Task

    busy_statuses = (TaskStatus.QUEUED, TaskStatus.DISPATCHING, TaskStatus.RUNNING)
    terminal = {
        TaskStatus.SUCCESS,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.TIMEOUT,
        TaskStatus.REJECTED,
        TaskStatus.SKIPPED,
    }
    try:
        threshold = datetime.now(UTC) - timedelta(seconds=STALE_TASK_STATUS_AGE_SECONDS)
        tasks = await Task.filter(status__in=list(busy_statuses)).only("id", "status").all()
        repaired = 0
        for task in tasks:
            latest = await TaskRun.filter(task_id=task.id).order_by("-id").only("id", "status", "created_at").first()
            if latest is None or latest.status not in terminal:
                continue
            if latest.created_at and latest.created_at > threshold:
                continue
            changed = await Task.filter(id=task.id, status=task.status).update(status=latest.status)
            repaired += int(changed)
            if changed:
                logger.warning(f"Task {task.id} 卡 {task.status}，按最新 run 收敛为 {latest.status}")
        if repaired:
            logger.info(f"修复卡死 Task 状态 {repaired} 个")
    except Exception:
        logger.exception("修复卡死 Task 状态失败")


__all__ = [
    "cleanup_zombie_tasks",
    "repair_inconsistent_states",
    "repair_stale_task_status",
    "repair_stuck_queued_runs",
]
