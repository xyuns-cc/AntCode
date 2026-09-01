"""Authoritative PostgreSQL eligibility for Worker lease issuance."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from antcode_core.application.services.workers.worker_registration_gate import (
    has_unacknowledged_v2_registration,
)
from antcode_core.domain.models import Worker, WorkerInstallKey, WorkerStatus
from antcode_core.infrastructure.redis import worker_heartbeat_key

# 按"管理态"否决，而不是按"观测态"否决。
#
# ``offline`` 是心跳监控**自动写入的观测结果**，不是运维意图。把它当作围栏会造成
# 闭合死锁：Worker 重启后心跳中断 → 被标记 offline → 拿不到 Lease → 而它必须先
# 拿到 Lease 才会重新上报心跳 → 永远回不来。真实环境里表现为容器无限重启
# （实测 45 次）且 ``direct-control/lease`` 恒 409。
#
# 放开 ``offline`` 不会削弱管理员停用：那条路径由 ``LeaseStore.disable_worker``
# 在 Redis 里装 lifecycle 围栏（``lease_service.py::disable_worker``），与本状态
# 字段相互独立，``store.grant`` 仍会被它拦下。
# 也不会造成双跑：回归的 Worker 拿到的是**新的 Lease 代际**，旧代际的结果会被
# fencing token 拒绝——这正是 Lease 代际机制存在的意义。
LEASE_ELIGIBLE_STATUSES = frozenset(
    {
        WorkerStatus.CONNECTING.value,
        WorkerStatus.ONLINE.value,
        WorkerStatus.OFFLINE.value,
    }
)


@dataclass(frozen=True, slots=True)
class WorkerLeaseEligibility:
    worker_id: str
    allowed: bool
    reason: str


WorkerLeaseAuthorizer = Callable[[str], Awaitable[WorkerLeaseEligibility]]


async def get_worker_lease_eligibility(worker_id: str) -> WorkerLeaseEligibility:
    """Read the current Worker lifecycle and V2 ACK state from PostgreSQL."""
    normalized_id = worker_id.strip()
    if not normalized_id:
        return WorkerLeaseEligibility(worker_id, False, "worker_id is empty")
    worker = await Worker.filter(public_id=normalized_id).only("public_id", "status").first()
    if worker is None:
        return WorkerLeaseEligibility(normalized_id, False, "Worker does not exist")
    if await has_unacknowledged_v2_registration(normalized_id):
        return WorkerLeaseEligibility(normalized_id, False, "Worker V2 registration is not acknowledged")
    status = str(worker.status or "").strip().lower()
    if status not in LEASE_ELIGIBLE_STATUSES:
        return WorkerLeaseEligibility(normalized_id, False, f"Worker status does not allow leases: {status}")
    return WorkerLeaseEligibility(normalized_id, True, "")


async def reconcile_worker_lease_lifecycle_fences(lease_store: Any) -> None:
    """Restore fences and revoke stale active state after Redis replacement."""
    # 只对"运维意图停用"装围栏。``offline`` 是心跳监控自动写入的观测态，
    # 在这里装围栏会让所有掉线过的 Worker 在 master 重启后永久无法回归
    # （与 LEASE_ELIGIBLE_STATUSES 处同一个观测态/管理态混用问题）。
    disabled_workers = await Worker.filter(status__in=[WorkerStatus.MAINTENANCE.value]).values_list(
        "public_id", "status"
    )
    pending_workers = await WorkerInstallKey.filter(
        registration_id__isnull=False,
        registration_acknowledged_at__isnull=True,
        used_by_worker__not_isnull=True,
    ).values_list("used_by_worker", flat=True)
    lifecycle = {str(worker_id): str(status) for worker_id, status in disabled_workers}
    for pending_worker_id in pending_workers:
        lifecycle.setdefault(str(pending_worker_id), "registration-pending")
    for lifecycle_worker_id, reason in lifecycle.items():
        await lease_store.disable_worker(
            lifecycle_worker_id,
            reason=reason,
            heartbeat_key=worker_heartbeat_key(lifecycle_worker_id, namespace=lease_store.namespace),
        )


__all__ = [
    "LEASE_ELIGIBLE_STATUSES",
    "WorkerLeaseAuthorizer",
    "WorkerLeaseEligibility",
    "get_worker_lease_eligibility",
    "reconcile_worker_lease_lifecycle_fences",
]
