"""Worker access checks for runtime routes."""

from __future__ import annotations

from antcode_core.application.services.users.user_service import user_service
from antcode_core.application.services.workers.worker_service import worker_service
from antcode_core.domain.models.worker import Worker
from fastapi import HTTPException


async def ensure_worker_access(worker_id: str, user_id: int) -> Worker:
    worker = await Worker.get_or_none(public_id=worker_id)
    if not worker:
        raise HTTPException(status_code=404, detail="Worker 不存在")
    if worker.status != "online":
        raise HTTPException(status_code=400, detail=f"Worker {worker.name} 当前不在线")

    is_admin = await user_service.is_admin(user_id)
    if is_admin:
        return worker

    allowed = await worker_service.check_user_worker_permission(
        user_id=user_id,
        worker_id=worker.id,
        is_admin=False,
        required_permission="use",
    )
    if not allowed:
        raise HTTPException(status_code=403, detail="无 Worker 访问权限")
    return worker
