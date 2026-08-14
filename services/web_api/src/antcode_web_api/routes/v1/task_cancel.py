"""任务取消原语（P1-FN-01，batch cancel 与 stop 端点共用）。

与单 run 取消端点（runs.py cancel）保持同一套语义：

1. 未分配 run 先走 ``cancel_unassigned_run`` 的 CAS —— 同步把
   dispatch_status 收敛为失败终态，成为 Master 派发 fence；只写 runtime
   状态时 dispatch 仍是 PENDING，Master 会照常派发已"取消"的 run。
2. 已分配 run 只负责可靠投递 control 取消指令，由 Worker 的真实执行结果
   写入 CANCELLED；API 不得提前制造终态，避免任务被删除后 Worker 无法结算。
3. 未分配 run 的 runtime CAS 结果必须如实上报。
"""

from datetime import UTC, datetime

from antcode_core.application.services.scheduler.scheduler_service import scheduler_service
from antcode_core.domain.models.enums import TaskStatus
from antcode_core.domain.models.task_run import TaskRun
from antcode_core.infrastructure.redis import build_cancel_control_payload, control_stream
from loguru import logger

# P1-FN-01: 取消未生效时构成"冲突"的终态集合(CANCELLED 除外,视为幂等成功)。
CANCEL_TERMINAL_CONFLICT_STATUSES = frozenset({"success", "failed", "timeout", "skipped", "rejected"})

CANCELLABLE_TASK_STATUSES = (
    TaskStatus.PENDING,
    TaskStatus.DISPATCHING,
    TaskStatus.QUEUED,
    TaskStatus.RUNNING,
)
UNASSIGNED_TASK_STATUSES = (
    TaskStatus.PENDING,
    TaskStatus.QUEUED,
    TaskStatus.DISPATCHING,
)


async def cancel_latest_task_run(task_id: str, user_id: int) -> bool:
    execution = await _resolve_latest_cancellable_run(task_id, user_id)
    if execution is None:
        return False
    if is_unassigned_task_run(execution):
        return await _cancel_unassigned_task_run(execution, user_id)
    return await _cancel_resolved_task_run(execution, user_id)


async def _cancel_unassigned_task_run(execution: TaskRun, user_id: int) -> bool:
    if await stop_unassigned_task_run(execution, user_id):
        return True
    current = await TaskRun.get_or_none(run_id=execution.run_id)
    if current is None:
        return False
    return await _cancel_resolved_task_run(current, user_id)


async def _cancel_resolved_task_run(execution: TaskRun, user_id: int) -> bool:
    if execution.worker_id:
        if not await record_task_cancel_request(execution.run_id, user_id):
            return await cancel_outcome_after_cas_miss(execution.run_id)
        return await try_send_task_cancel(execution, user_id)
    if await mark_task_run_cancelled(execution.run_id, user_id):
        return True
    return await cancel_outcome_after_cas_miss(execution.run_id)


async def record_task_cancel_request(run_id: str, user_id: int) -> bool:
    from antcode_core.application.services.scheduler.cancel_request_service import (
        record_cancel_request,
    )

    return await record_cancel_request(run_id, requested_by=user_id)


async def _resolve_latest_cancellable_run(task_id: str, user_id: int) -> TaskRun | None:
    task = await scheduler_service.get_task_by_id(task_id, user_id)
    if not task:
        return None
    return await latest_cancellable_run(task.id)


async def cancel_outcome_after_cas_miss(run_id: str) -> bool:
    """runtime CAS 未生效时按单取消端点语义折算批量结果。

    - 已是 CANCELLED → 幂等成功;
    - 已进入其它终态 → 冲突(False),对齐 runs.py 的 409 语义;
    - 仍是非终态 → 取消指令已发出,最终状态由 Worker 结果决定(视为成功)。
    """
    final = await TaskRun.get_or_none(run_id=run_id)
    if final is None:
        return False
    final_status = final.status.value if final.status else ""
    if final_status == TaskStatus.CANCELLED.value:
        return True
    return final_status not in CANCEL_TERMINAL_CONFLICT_STATUSES


async def latest_cancellable_run(task_id: int) -> TaskRun | None:
    return (
        await TaskRun.filter(
            task_id=task_id,
            status__in=list(CANCELLABLE_TASK_STATUSES),
        )
        .order_by("-created_at")
        .first()
    )


def is_unassigned_task_run(execution: TaskRun) -> bool:
    return execution.worker_id is None and execution.status in UNASSIGNED_TASK_STATUSES


async def stop_unassigned_task_run(execution: TaskRun, user_id: int) -> bool:
    # P1-FN-02: 与 /runs/{run_id}/cancel 共用核心服务实现（同步收敛
    # dispatch_status + 回写 Task 状态），杜绝双份取消逻辑漂移。
    from antcode_core.application.services.scheduler.execution_status_service import execution_status_service

    return await execution_status_service.cancel_unassigned_run(execution.run_id, user_id)


async def try_send_task_cancel(execution: TaskRun, user_id: int) -> bool:
    try:
        return await send_task_cancel(execution, user_id)
    except Exception as exc:
        logger.warning(f"发送取消指令失败: {exc}")
        return False


async def send_task_cancel(execution: TaskRun, user_id: int) -> bool:
    from antcode_core.application.services.runtime.runtime_control_service import write_control_event
    from antcode_core.application.services.workers.worker_service import worker_service
    from antcode_core.infrastructure.redis import get_redis_client

    worker = await worker_service.get_worker_by_id(execution.worker_id)
    if not worker:
        return False
    redis = await get_redis_client()
    payload = build_cancel_control_payload(run_id=execution.run_id, reason=f"user_cancel:{user_id}")
    await write_control_event(redis, control_stream(worker.public_id), payload)
    return True


async def mark_task_run_cancelled(run_id: str, user_id: int) -> bool:
    from antcode_core.application.services.scheduler.execution_status_service import execution_status_service

    return bool(
        await execution_status_service.update_runtime_status(
            run_id=run_id,
            status="cancelled",
            status_at=datetime.now(UTC),
            error_message=f"用户取消 (user_id={user_id})",
        )
    )


__all__ = [
    "CANCELLABLE_TASK_STATUSES",
    "CANCEL_TERMINAL_CONFLICT_STATUSES",
    "UNASSIGNED_TASK_STATUSES",
    "cancel_latest_task_run",
    "cancel_outcome_after_cas_miss",
    "is_unassigned_task_run",
    "latest_cancellable_run",
    "mark_task_run_cancelled",
    "record_task_cancel_request",
    "send_task_cancel",
    "stop_unassigned_task_run",
    "try_send_task_cancel",
]
