"""Serialize Worker deletion against task-run dispatch binding."""

from fastapi import HTTPException, status
from tortoise.transactions import in_transaction

from antcode_core.domain.models import TaskRun, Worker, WorkerStatus

ACTIVE_RUN_STATUSES = ("pending", "dispatching", "queued", "running")


async def quiesce_worker_for_delete(worker: Worker) -> None:
    """Lock the Worker, reject active runs, then make future binds fail closed."""
    async with in_transaction("default") as connection:
        locked = await Worker.filter(id=worker.id).using_db(connection).select_for_update().first()
        if locked is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker 不存在")
        active = await TaskRun.filter(worker_id=worker.id, status__in=ACTIVE_RUN_STATUSES).using_db(connection).count()
        if active:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Worker {worker.name} 仍有 {active} 个未终态执行，请先取消或等待完成后再删除",
            )
        await Worker.filter(id=worker.id).using_db(connection).update(status=WorkerStatus.MAINTENANCE)
    worker.status = WorkerStatus.MAINTENANCE


__all__ = ["ACTIVE_RUN_STATUSES", "quiesce_worker_for_delete"]
