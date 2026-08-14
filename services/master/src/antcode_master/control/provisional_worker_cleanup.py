"""Leader-fenced settlement for expired provisional Worker executions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from antcode_core.application.services.scheduler.task_run_counters import task_run_outcome_counts
from antcode_core.application.services.workers.run_ownership_fence import (
    ownership_token,
    release_run_ownership,
    run_owner_key,
)
from antcode_core.application.services.workers.worker_delete_guard import ACTIVE_RUN_STATUSES
from antcode_core.domain.models import Task, TaskRun, Worker
from antcode_core.domain.models.enums import DispatchStatus, RuntimeStatus, TaskStatus
from antcode_core.infrastructure.redis import get_redis_client, redis_namespace
from tortoise.transactions import in_transaction

from antcode_master.control.scheduler_authority import require_scheduler_authority
from antcode_master.leader import require_fencing_token

_SETTLEMENT_REASON = "Worker 注册确认窗口已过期"
OWNERSHIP_RELEASE_BATCH_SIZE = 200


async def settle_expired_provisional_worker_runs(worker_internal_id: int) -> int:
    """在删除临时 Worker 前，将其全部活跃执行收敛为失败终态。"""
    authority_token = await require_fencing_token()
    now = datetime.now(UTC)
    worker = await Worker.filter(id=worker_internal_id).only("public_id").first()
    if worker is None:
        raise RuntimeError("待清理的临时 Worker 不存在")
    async with in_transaction("default") as connection:
        await require_scheduler_authority(connection, authority_token)
        task_ids = await _load_active_task_ids(connection, worker_internal_id)
        await _lock_tasks(connection, task_ids)
        await require_scheduler_authority(connection, authority_token)
        runs = await _load_active_runs(connection, worker_internal_id)
        for run in runs:
            await _settle_run(connection, run, now)
        await _sync_tasks(connection, runs)
    await _release_bound_run_ownerships(worker.public_id, worker_internal_id)
    return len(runs)


async def _load_active_task_ids(connection: Any, worker_internal_id: int) -> list[int]:
    values = cast(
        list[int],
        await TaskRun.filter(worker_id=worker_internal_id, status__in=list(ACTIVE_RUN_STATUSES))
        .using_db(connection)
        .values_list("task_id", flat=True),
    )
    return sorted(set(values))


async def _load_active_runs(connection: Any, worker_internal_id: int) -> list[TaskRun]:
    return await (
        TaskRun.filter(worker_id=worker_internal_id, status__in=list(ACTIVE_RUN_STATUSES))
        .using_db(connection)
        .select_for_update()
        .order_by("id")
        .all()
    )


async def _lock_tasks(connection: Any, task_ids: list[int]) -> None:
    if not task_ids:
        return
    locked = await Task.filter(id__in=task_ids).using_db(connection).select_for_update().only("id").all()
    if len(locked) != len(task_ids):
        raise RuntimeError("临时 Worker 活跃执行关联任务缺失，拒绝静默解绑")


async def _settle_run(connection: Any, run: TaskRun, now: datetime) -> None:
    updates: dict[str, Any] = {
        "status": TaskStatus.FAILED,
        "runtime_status": RuntimeStatus.FAILED,
        "runtime_updated_at": now,
        "last_heartbeat": now,
        "end_time": now,
        "error_message": _SETTLEMENT_REASON,
        "next_retry_at": None,
    }
    if run.dispatch_status != DispatchStatus.ACKED:
        updates.update(dispatch_status=DispatchStatus.FAILED, dispatch_updated_at=now)
    if run.start_time is not None:
        updates["duration_seconds"] = max(0.0, (now - run.start_time).total_seconds())
    updated = await (
        TaskRun.filter(id=run.id, worker_id=run.worker_id, status__in=list(ACTIVE_RUN_STATUSES))
        .using_db(connection)
        .update(**updates)
    )
    if updated != 1:
        raise RuntimeError(f"临时 Worker 执行结算 CAS 失败: run_id={run.run_id}")


async def _sync_tasks(connection: Any, runs: list[TaskRun]) -> None:
    settled_ids = {run.id for run in runs}
    for task_id in sorted({run.task_id for run in runs}):
        updates: dict[str, Any] = await task_run_outcome_counts(connection, task_id)
        latest = await TaskRun.filter(task_id=task_id).using_db(connection).order_by("-id").only("id").first()
        if latest is not None and latest.id in settled_ids:
            updates["status"] = TaskStatus.FAILED
        await Task.filter(id=task_id).using_db(connection).update(**updates)


async def _release_bound_run_ownerships(worker_public_id: str, worker_internal_id: int) -> None:
    redis = await get_redis_client()
    namespace = redis_namespace()
    after_id = 0
    while True:
        runs = await (
            TaskRun.filter(worker_id=worker_internal_id, id__gt=after_id)
            .only("id", "run_id", "lease_id")
            .order_by("id")
            .limit(OWNERSHIP_RELEASE_BATCH_SIZE)
            .all()
        )
        if not runs:
            return
        await _release_ownership_batch(
            redis,
            namespace=namespace,
            worker_public_id=worker_public_id,
            runs=runs,
        )
        after_id = runs[-1].id


async def _release_ownership_batch(
    redis: Any,
    *,
    namespace: str,
    worker_public_id: str,
    runs: list[TaskRun],
) -> None:
    owners = await redis.mget([run_owner_key(run.run_id, namespace) for run in runs])
    if len(owners) != len(runs):
        raise RuntimeError("临时 Worker ownership 查询结果数量不匹配")
    for run, raw_owner in zip(runs, owners, strict=True):
        if raw_owner is None:
            continue
        owner = raw_owner.decode() if isinstance(raw_owner, bytes) else str(raw_owner)
        if not run.lease_id or owner != ownership_token(worker_public_id, run.lease_id):
            raise RuntimeError(f"临时 Worker 执行 ownership 与 PostgreSQL 不一致: run_id={run.run_id}")
        released = await release_run_ownership(
            redis,
            worker_id=worker_public_id,
            lease_id=run.lease_id,
            run_id=run.run_id,
            namespace=namespace,
        )
        if not released:
            raise RuntimeError(f"临时 Worker 执行 ownership 精确撤销失败: run_id={run.run_id}")


__all__ = ["settle_expired_provisional_worker_runs"]
