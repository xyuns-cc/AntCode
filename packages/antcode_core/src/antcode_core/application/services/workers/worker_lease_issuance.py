"""Fail-closed Worker lease issuance around PostgreSQL lifecycle authority."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from antcode_contracts.wire_contract import require_supported_wire_contract

from antcode_core.application.services.lease_service import Lease, LeaseStore
from antcode_core.application.services.workers.worker_lease_authority import (
    WorkerLeaseAuthorizer,
    WorkerLeaseEligibility,
)


class WorkerLeaseRejected(RuntimeError):
    def __init__(self, eligibility: WorkerLeaseEligibility):
        self.eligibility = eligibility
        super().__init__(eligibility.reason)


@dataclass(frozen=True, slots=True)
class WorkerLeaseGrantRequest:
    worker_id: str
    current_lease_id: str = ""
    metrics: dict[str, Any] | None = None
    capabilities: dict[str, Any] | None = None


async def grant_authorized_worker_lease(
    store: LeaseStore,
    authorizer: WorkerLeaseAuthorizer,
    request: WorkerLeaseGrantRequest,
) -> Lease:
    """签发 Lease 前先卡线协议契约，再卡 PostgreSQL 生命周期权威。

    契约校验放在这里而不是两个调用点，是因为这里是 Gateway（gRPC Lease）与
    Direct（HTTP direct-control/lease）唯一共用的签发入口：版本错配的 Worker
    在两种传输下都拿不到 Lease，也就拿不到心跳与派发前置条件。
    """
    require_supported_wire_contract(request.capabilities)
    await _require_eligible(authorizer, request.worker_id)
    lease = await store.grant(
        request.worker_id,
        current_lease_id=request.current_lease_id,
        metrics=request.metrics,
        capabilities=request.capabilities,
    )
    try:
        await _require_eligible(authorizer, request.worker_id)
    except Exception:
        await _revoke_granted_lease(store, lease)
        raise
    return lease


async def _require_eligible(authorizer: WorkerLeaseAuthorizer, worker_id: str) -> None:
    eligibility = await authorizer(worker_id)
    if not eligibility.allowed:
        raise WorkerLeaseRejected(eligibility)


async def _revoke_granted_lease(store: LeaseStore, lease: Lease) -> None:
    revoked = await store.revoke(
        lease.worker_id,
        reason="authoritative lifecycle changed during grant",
        lease_id=lease.lease_id,
    )
    if not revoked:
        raise RuntimeError("Lease eligibility changed but the issued generation could not be revoked")


__all__ = [
    "WorkerLeaseGrantRequest",
    "WorkerLeaseRejected",
    "grant_authorized_worker_lease",
]
