"""Fence Worker lease eviction side effects with scheduler authority."""

from __future__ import annotations

from datetime import UTC, datetime

from antcode_core.application.services.lease_service import LeaseStore
from antcode_core.domain.models import TaskRun, Worker
from antcode_core.domain.models.enums import RuntimeStatus, TaskStatus, WorkerStatus
from loguru import logger
from tortoise.expressions import Q

from antcode_master.control.reconcile_failure_settlement import settle_runtime_failure_snapshot
from antcode_master.control.scheduler_authority import execute_with_scheduler_authority
from antcode_master.leader import require_fencing_token

_lease_store: LeaseStore | None = None


def configure_lease_store(lease_store: LeaseStore) -> None:
    global _lease_store
    _lease_store = lease_store


async def on_worker_evicted(worker_id: str, evicted_lease_id: str = "") -> None:
    authority_token = await require_fencing_token()
    await apply_worker_eviction(
        worker_id,
        evicted_lease_id,
        authority_token=authority_token,
    )


async def apply_worker_eviction(
    worker_id: str,
    evicted_lease_id: str,
    *,
    authority_token: int,
) -> None:
    worker = await Worker.filter(public_id=worker_id).first()
    if worker is None:
        return
    superseded = await _eviction_was_superseded(worker_id, evicted_lease_id)
    if superseded and not evicted_lease_id:
        logger.info("跳过代际未知的 eviction（worker 已持有有效 lease）: worker_id={}", worker_id)
        return
    running = await _load_running(worker.id, evicted_lease_id, superseded)
    recovered = not superseded and await _eviction_was_superseded(worker_id, evicted_lease_id)
    if recovered:
        logger.info("失效前复核发现 worker 已以新代际回归,跳过 NULL-lease run: worker_id={}", worker_id)
    if not superseded and not recovered and worker.status == WorkerStatus.ONLINE.value:
        await execute_with_scheduler_authority(
            authority_token,
            _mark_worker_offline,
            worker_internal_id=worker.id,
        )
    failed, skipped = await _settle_running(running, authority_token, recovered)
    logger.info(
        "Lease 失租回收完成: worker_id={} marked_failed={}/{} skipped_null={}",
        worker_id,
        failed,
        len(running),
        skipped,
    )


async def _eviction_was_superseded(worker_id: str, evicted_lease_id: str) -> bool:
    if _lease_store is None:
        raise RuntimeError("LeaseStore 尚未初始化，拒绝执行 eviction 副作用")
    current = await _lease_store.get(worker_id, include_expired=False)
    if current is None or not current.lease_id:
        return False
    if evicted_lease_id and current.lease_id == evicted_lease_id:
        return False
    logger.info("忽略旧 Lease eviction（worker 已持有新代际有效 lease）: worker_id={}", worker_id)
    return True


async def _load_running(
    worker_internal_id: int,
    evicted_lease_id: str,
    superseded: bool,
) -> list[TaskRun]:
    query = TaskRun.filter(worker_id=worker_internal_id, status=TaskStatus.RUNNING)
    if superseded:
        query = query.filter(lease_id=evicted_lease_id)
    elif evicted_lease_id:
        query = query.filter(Q(lease_id=evicted_lease_id) | Q(lease_id__isnull=True))
    return await query.only(
        "id",
        "run_id",
        "scheduler_fencing_token",
        "dispatch_status",
        "runtime_status",
        "worker_id",
        "lease_id",
    ).all()


async def _settle_running(
    running: list[TaskRun],
    authority_token: int,
    skip_null_lease: bool,
) -> tuple[int, int]:
    failed = 0
    skipped = 0
    status_at = datetime.now(UTC)
    for run in running:
        if skip_null_lease and not run.lease_id:
            skipped += 1
            continue
        settled = await settle_runtime_failure_snapshot(
            run,
            authority_token,
            terminal_status=RuntimeStatus.FAILED,
            error_message="Worker 失联（lease expired）",
            status_at=status_at,
        )
        failed += int(settled)
    return failed, skipped


async def _mark_worker_offline(*, worker_internal_id: int) -> bool:
    updated = await Worker.filter(
        id=worker_internal_id,
        status=WorkerStatus.ONLINE,
    ).update(status=WorkerStatus.OFFLINE)
    return updated == 1


__all__ = ["apply_worker_eviction", "configure_lease_store", "on_worker_evicted"]
