"""PostgreSQL and Redis fencing primitives for Master scheduler effects."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from antcode_core.application.services.lease_fenced_ready_publish import scheduler_dispatch_epoch
from antcode_core.domain.models.enums import DispatchStatus, TaskStatus
from antcode_core.domain.models.scheduler_authority import (
    SCHEDULER_AUTHORITY_NAME,
    SchedulerAuthority,
)
from antcode_core.domain.models.task import Task
from antcode_core.domain.models.task_run import TaskRun
from tortoise.expressions import Q
from tortoise.transactions import in_transaction


class SchedulerAuthorityLost(RuntimeError):
    """The caller's Master epoch is no longer authoritative."""


class SchedulerAuthorityNotActivated(SchedulerAuthorityLost):
    """本任期的 epoch 还没落 PG —— 与"被更高任期顶掉"是相反方向的事实。

    Leader 锁由最先轮询到的那个 loop 抢下（``lease_sweeper`` 每秒一次，最快），
    而 epoch 落库由 ``scheduler_loop`` 的 watcher 在下一次 poll 时才做，两者之间
    存在一个真实窗口（冷启动是亚秒级竞态，故障切换后可长达一个 watcher 轮询周期）。
    窗口内读到的 ``current`` 只会为空或小于本任期，永远不可能大于本任期：
    fencing token 单调递增且只发一次，比我小的代际不可能是抢走我权威的人。
    等一轮就能自愈的"还没就绪"和必须立刻交权的"已被顶掉"共用一个信号，会让
    启动期噪声长得和数据面事故一模一样。仍继承 ``SchedulerAuthorityLost``：
    两者对写入方的约束相同——都不许写。
    """


async def activate_scheduler_authority(token: int) -> None:
    """Advance the PostgreSQL epoch at Leader activation."""
    if token < 1:
        raise ValueError("scheduler fencing token must be positive")
    async with in_transaction("default") as conn:
        rows = await conn.execute_query_dict(
            """
            INSERT INTO scheduler_authority (name, fencing_token, activated_at)
            VALUES ($1, $2, $3)
            ON CONFLICT (name) DO UPDATE
               SET fencing_token = EXCLUDED.fencing_token,
                   activated_at = EXCLUDED.activated_at
             WHERE scheduler_authority.fencing_token <= EXCLUDED.fencing_token
            RETURNING fencing_token
            """,
            [SCHEDULER_AUTHORITY_NAME, token, datetime.now(UTC)],
        )
    if not rows or int(rows[0]["fencing_token"]) != token:
        raise SchedulerAuthorityLost(f"stale scheduler activation token: {token}")


async def require_scheduler_authority(connection: Any, token: int) -> None:
    authority = (
        await SchedulerAuthority.filter(name=SCHEDULER_AUTHORITY_NAME).using_db(connection).select_for_update().first()
    )
    current = int(authority.fencing_token) if authority is not None else None
    if current == token:
        return
    if current is None or current < token:
        raise SchedulerAuthorityNotActivated(f"scheduler epoch 尚未激活: expected={token} current={current}")
    raise SchedulerAuthorityLost(f"scheduler authority changed: expected={token} current={current}")


async def claim_dispatch(run_id: str, token: int, status_at: datetime) -> bool:
    """Atomically validate the epoch and claim a pending run for dispatch."""
    async with in_transaction("default") as conn:
        await require_scheduler_authority(conn, token)
        return bool(
            await TaskRun.filter(
                run_id=run_id,
                scheduler_fencing_token=token,
                dispatch_status=DispatchStatus.PENDING,
                cancel_requested_at__isnull=True,
            )
            .using_db(conn)
            .update(
                dispatch_status=DispatchStatus.DISPATCHING,
                dispatch_updated_at=status_at,
                status=TaskStatus.DISPATCHING,
            )
        )


async def take_over_pre_dispatch_run(run_id: str, token: int) -> TaskRun | None:
    """Move an unpublished run from an older epoch to the current epoch."""
    async with in_transaction("default") as conn:
        await require_scheduler_authority(conn, token)
        pending = Q(dispatch_status=DispatchStatus.PENDING) & (
            Q(scheduler_fencing_token__isnull=True) | Q(scheduler_fencing_token__lte=token)
        )
        stale_dispatching = Q(dispatch_status=DispatchStatus.DISPATCHING) & (
            Q(scheduler_fencing_token__isnull=True) | Q(scheduler_fencing_token__lt=token)
        )
        query = TaskRun.filter(
            pending | stale_dispatching,
            run_id=run_id,
            status__in=[TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.DISPATCHING],
            runtime_status__isnull=True,
            cancel_requested_at__isnull=True,
        )
        updated = await query.using_db(conn).update(
            scheduler_fencing_token=token,
            dispatch_status=DispatchStatus.PENDING,
            dispatch_updated_at=datetime.now(UTC),
            status=TaskStatus.QUEUED,
            worker_id=None,
            lease_id=None,
            lease_gen=None,
        )
        if updated != 1:
            return None
        return await TaskRun.filter(run_id=run_id).using_db(conn).first()


async def dispatch_with_scheduler_epoch(token: int, function: Any, **kwargs: Any) -> Any:
    """Keep the Redis publish fence active for an entire dispatch call tree."""
    with scheduler_dispatch_epoch(token):
        return await function(**kwargs)


async def execute_with_scheduler_authority(token: int, function: Any, **kwargs: Any) -> Any:
    """Serialize a scheduler-owned write with Leader activation."""
    async with in_transaction("default") as conn:
        await require_scheduler_authority(conn, token)
        return await function(**kwargs)


async def complete_one_time_schedule(task_id: int, run_id: str, token: int) -> bool:
    """Deactivate a one-time task only after its deterministic run is durable."""
    async with in_transaction("default") as conn:
        await require_scheduler_authority(conn, token)
        owned_run = (
            await TaskRun.filter(
                run_id=run_id,
                task_id=task_id,
            )
            .using_db(conn)
            .exists()
        )
        if not owned_run:
            raise RuntimeError(f"one-time run disappeared before completion: {run_id}")
        updated = (
            await Task.filter(id=task_id, is_active=True)
            .using_db(conn)
            .update(
                is_active=False,
                next_run_time=None,
            )
        )
        return bool(updated)


async def reset_unpublished_dispatch(run_id: str, token: int) -> bool:
    """Release a dispatch claim after the atomic Redis fence rejected XADD."""
    updated = await TaskRun.filter(
        run_id=run_id,
        scheduler_fencing_token=token,
        dispatch_status=DispatchStatus.DISPATCHING,
        runtime_status__isnull=True,
    ).update(
        dispatch_status=DispatchStatus.PENDING,
        dispatch_updated_at=datetime.now(UTC),
        status=TaskStatus.QUEUED,
        worker_id=None,
        lease_id=None,
        lease_gen=None,
    )
    return bool(updated)


__all__ = [
    "SchedulerAuthorityLost",
    "SchedulerAuthorityNotActivated",
    "activate_scheduler_authority",
    "claim_dispatch",
    "complete_one_time_schedule",
    "dispatch_with_scheduler_epoch",
    "execute_with_scheduler_authority",
    "require_scheduler_authority",
    "reset_unpublished_dispatch",
    "take_over_pre_dispatch_run",
]
