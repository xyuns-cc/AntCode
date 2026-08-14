"""Redis lifecycle fence used by administrative Worker state transitions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from antcode_core.application.services.lease_service import LeaseStore
from antcode_core.domain.models import WorkerStatus
from antcode_core.infrastructure.redis import worker_heartbeat_key


@dataclass(frozen=True, slots=True)
class WorkerLeaseLifecycleFence:
    redis: Any
    lease_store: LeaseStore

    async def disable(self, worker_id: str, reason: str) -> bool:
        """Atomically block grants, revoke the current generation, and clear heartbeat."""
        return await self.lease_store.disable_worker(
            worker_id,
            reason=reason,
            heartbeat_key=worker_heartbeat_key(worker_id, namespace=self.lease_store.namespace),
        )

    async def enable(self, worker_id: str, *, expected_reasons: tuple[str, ...]) -> bool:
        """Clear an administrative fence after PostgreSQL accepts an active state."""
        return await self.lease_store.enable_worker(worker_id, expected_reasons=expected_reasons)

    async def enable_registration(self, worker_id: str) -> bool:
        """Clear only the V2 registration fence, preserving administrative fences."""
        return await self.lease_store.enable_worker(
            worker_id,
            expected_reasons=("registration-pending",),
            allow_mismatch=True,
        )


def reconnectable_fence_reasons(previous_status: str) -> tuple[str, ...]:
    status_value = str(previous_status or "").strip().lower()
    if status_value == WorkerStatus.MAINTENANCE.value:
        return (f"status:{status_value}", status_value)
    if status_value == WorkerStatus.OFFLINE.value:
        return ("status:offline", "offline", "disconnect")
    raise RuntimeError(f"Worker 不能从 {status_value or 'unknown'} 状态清除 lifecycle fence")


__all__ = ["WorkerLeaseLifecycleFence", "reconnectable_fence_reasons"]
