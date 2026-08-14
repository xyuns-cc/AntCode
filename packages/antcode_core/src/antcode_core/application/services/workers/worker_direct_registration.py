"""Direct Worker proof-registration orchestration."""

from __future__ import annotations

from typing import Any

from antcode_core.application.services.workers.worker_lease_lifecycle import reconnectable_fence_reasons
from antcode_core.domain.models import WorkerStatus


async def register_direct_worker(
    request: Any,
    *,
    service: Any,
) -> tuple[Any, bool]:
    existing = await service.get_worker_by_public_id(request.worker_id)
    if existing and existing.status == WorkerStatus.MAINTENANCE.value:
        return await service._connection_service.register_direct_worker(request)
    if existing and existing.status != WorkerStatus.OFFLINE.value:
        raise RuntimeError(f"Direct Worker 状态不允许重新注册: {existing.status}")
    if existing:
        reasons = reconnectable_fence_reasons(str(existing.status))
        await service._lease_enabler(existing.public_id, reasons)
    return await service._connection_service.register_direct_worker(request)


__all__ = ["register_direct_worker"]
