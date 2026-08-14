"""Authoritative precondition for deleting an expired provisional Worker."""

from __future__ import annotations

from typing import Any

from tortoise.transactions import in_transaction

from antcode_core.domain.models import Worker, WorkerInstallKey, WorkerStatus


class ProvisionalWorkerCleanupRejected(RuntimeError):
    """Worker 不属于已过期且未确认的临时注册。"""


async def mark_expired_provisional_worker_maintenance(worker: Worker) -> None:
    """在外部身份撤销前关闭 PostgreSQL 派发准入。"""
    async with in_transaction("default") as connection:
        locked = await Worker.filter(id=worker.id).using_db(connection).select_for_update().first()
        if locked is None:
            raise ProvisionalWorkerCleanupRejected("过期注册 Worker 已不存在")
        if not await _expired_registration_exists(locked.public_id, connection):
            raise ProvisionalWorkerCleanupRejected("Worker 不属于已过期且未确认的临时注册")
        await Worker.filter(id=locked.id).using_db(connection).update(status=WorkerStatus.MAINTENANCE)
    worker.status = WorkerStatus.MAINTENANCE


async def _expired_registration_exists(worker_public_id: str, connection: Any) -> bool:
    return await (
        WorkerInstallKey.filter(
            used_by_worker=worker_public_id,
            status="expired",
            registration_id__isnull=False,
            registration_acknowledged_at__isnull=True,
        )
        .using_db(connection)
        .exists()
    )


__all__ = [
    "ProvisionalWorkerCleanupRejected",
    "mark_expired_provisional_worker_maintenance",
]
