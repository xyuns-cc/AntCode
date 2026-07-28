"""P1-FN-03/P1-FN-04: 派发绑定的状态 CAS 与 Worker 行锁。

把"待派发 run 绑定到 Worker"收敛为单事务原子操作：

- **P1-FN-04**：绑定与 Worker 删除共用同一把行锁 —— worker_service
  ``_cascade_delete_worker_data`` 在删除事务里对 Worker 行
  ``SELECT ... FOR UPDATE`` 并复检活跃 run；这里先锁同一行并确认 Worker
  未删除且在线，再写无 FK 的 ``TaskRun.worker_id``。两侧互斥：删除先提交
  → 本事务锁到行缺失，中止派发；本事务先提交 → 删除侧行锁后的活跃 run
  复检看到新绑定 run，中止删除。
- **P1-FN-03**：绑定带可派发状态 CAS，终态/运行中/已取消的 run 逐条显式
  冲突（``DispatchStateConflictError``），禁止静默重写 Worker 后再次入队
  重放外部副作用。
"""

from __future__ import annotations

from loguru import logger
from tortoise.transactions import in_transaction

from antcode_core.domain.models import Worker, WorkerStatus
from antcode_core.domain.models.enums import DispatchStatus, TaskStatus


class DispatchStateConflictError(RuntimeError):
    """P1-FN-03: run 处于不可重派状态(运行中/已确认/终态/已取消)时的派发冲突。"""


# P1-FN-03: 可派发状态守卫。
# - scoped(web_api 批量分发)只允许首次派发(PENDING)或派发层已判失败的
#   显式重派(FAILED);
# - legacy(Master scheduler / redispatch / 直接分发)额外允许 DISPATCHING
#   —— scheduler 在调用派发前已把 dispatch_status CAS 到 DISPATCHING。
# 两者都要求 runtime 从未启动(NULL)且未被取消(status != CANCELLED;
# 取消路径写 status=CANCELLED + dispatch=FAILED,单靠 dispatch 集合挡不住)。
SCOPED_DISPATCHABLE_STATUSES = (DispatchStatus.PENDING, DispatchStatus.FAILED)
LEGACY_DISPATCHABLE_STATUSES = (
    DispatchStatus.PENDING,
    DispatchStatus.DISPATCHING,
    DispatchStatus.FAILED,
)


async def bind_task_runs_to_worker(tasks: list[dict], worker_id: int) -> int:
    """把待派发 run 绑定到 Worker（事务内 Worker 行锁 + 状态 CAS）。"""
    async with in_transaction() as conn:
        await _lock_dispatch_worker(conn, worker_id)
        updated = await _bind_scoped_runs(conn, tasks, worker_id)
        updated += await _bind_legacy_runs(conn, tasks, worker_id)
    return updated


async def _lock_dispatch_worker(conn, worker_id: int) -> None:
    """P1-FN-04: 行锁 + 复检目标 Worker 仍存在且在线，与删除侧串行化。"""
    locked = await Worker.filter(id=worker_id).using_db(conn).select_for_update().first()
    if locked is None:
        raise RuntimeError(f"目标 Worker 已被删除,中止派发: worker_id={worker_id}")
    if locked.status != WorkerStatus.ONLINE:
        raise RuntimeError(f"目标 Worker 非在线状态({locked.status}),中止派发: worker_id={worker_id}")


def _dispatchable_runs(conn, allowed_dispatch, **filters):
    """构造"仍可派发"的 TaskRun 查询（状态 CAS 谓词的单一实现）。"""
    from antcode_core.domain.models import TaskRun

    return (
        TaskRun.filter(
            dispatch_status__in=list(allowed_dispatch),
            runtime_status__isnull=True,
            **filters,
        )
        .exclude(status=TaskStatus.CANCELLED)
        .using_db(conn)
    )


async def _bind_scoped_runs(conn, tasks: list[dict], worker_id: int) -> int:
    from antcode_core.domain.models import Task

    updated = 0
    for task in (item for item in tasks if item.get("_dispatch_scope")):
        scope = task["_dispatch_scope"]
        authorized = (
            await Task.filter(
                id=scope["task_id"],
                project_id=scope["project_id"],
                user_id=scope["owner_id"],
            )
            .using_db(conn)
            .exists()
        )
        if not authorized:
            raise RuntimeError("批量派发作用域已失效")
        bound = await _dispatchable_runs(
            conn,
            SCOPED_DISPATCHABLE_STATUSES,
            run_id=scope["run_id"],
            task_id=scope["task_id"],
        ).update(worker_id=worker_id)
        if bound != 1:
            # P1-FN-03: 终态/运行中/已取消 run 逐条显式冲突,禁止静默重写
            # Worker 后照常入队重放外部副作用。
            raise DispatchStateConflictError(f"运行记录不可重派(状态冲突或已失效): run_id={scope['run_id']}")
        updated += bound
    return updated


async def _bind_legacy_runs(conn, tasks: list[dict], worker_id: int) -> int:
    run_ids = {
        str(run_id)
        for task in tasks
        if not task.get("_dispatch_scope")
        if (run_id := task.get("run_id") or task.get("task_id")) not in (None, "")
    }
    if not run_ids:
        return 0
    bound = await _dispatchable_runs(
        conn,
        LEGACY_DISPATCHABLE_STATUSES,
        run_id__in=run_ids,
    ).update(worker_id=worker_id)
    if bound != len(run_ids):
        await _raise_on_legacy_bind_conflicts(conn, run_ids)
    return bound


async def _raise_on_legacy_bind_conflicts(conn, run_ids: set[str]) -> None:
    """区分"状态冲突"（显式失败）与"无 TaskRun 记录"（兼容告警放行）。"""
    from antcode_core.domain.models import TaskRun

    rows = (
        await TaskRun.filter(run_id__in=run_ids)
        .using_db(conn)
        .only("run_id", "status", "dispatch_status", "runtime_status")
        .all()
    )
    conflicted = sorted(
        row.run_id
        for row in rows
        if not (
            row.dispatch_status in LEGACY_DISPATCHABLE_STATUSES
            and row.runtime_status is None
            and row.status != TaskStatus.CANCELLED
        )
    )
    if conflicted:
        # P1-FN-03: 状态冲突逐条上抛;调用方(scheduler/redispatch)按派发
        # 失败处理,redispatch 的 attempts 上限最终收敛,不会静默重放。
        raise DispatchStateConflictError(f"运行记录不可重派(状态冲突): run_ids={conflicted}")
    missing = sorted(run_ids - {row.run_id for row in rows})
    if missing:
        # 兼容历史调用:不存在 TaskRun 的 run 保持旧行为(告警放行),
        # 后续该 run 的上报仍会被 ownership 校验拒绝。
        logger.warning("部分派发任务没有 TaskRun 归属记录: missing={}", missing)


__all__ = [
    "LEGACY_DISPATCHABLE_STATUSES",
    "SCOPED_DISPATCHABLE_STATUSES",
    "DispatchStateConflictError",
    "bind_task_runs_to_worker",
]
