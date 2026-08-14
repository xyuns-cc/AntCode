from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from antcode_contracts.wire_contract import wire_contract_capability
from antcode_core.application.services.lease_service import Lease
from antcode_core.application.services.workers import worker_lease_authority as authority
from antcode_core.application.services.workers.worker_lease_issuance import (
    WorkerLeaseGrantRequest,
    WorkerLeaseRejected,
    grant_authorized_worker_lease,
)
from antcode_core.domain.models import Worker


class _WorkerQuery:
    def __init__(self, worker):
        self.first = AsyncMock(return_value=worker)

    def only(self, *_fields):
        return self


class _ValuesQuery:
    def __init__(self, rows):
        self.rows = rows

    async def values_list(self, *_fields, **_kwargs):
        return self.rows


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["connecting", "online"])
async def test_acknowledged_active_worker_is_eligible(status: str) -> None:
    worker = SimpleNamespace(public_id="worker-1", status=status)
    with (
        patch.object(Worker, "filter", return_value=_WorkerQuery(worker)),
        patch.object(authority, "has_unacknowledged_v2_registration", AsyncMock(return_value=False)),
    ):
        result = await authority.get_worker_lease_eligibility("worker-1")

    assert result.allowed is True


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["maintenance"])
async def test_administratively_disabled_worker_is_ineligible(status: str) -> None:
    """只有"运维意图停用"才否决签发。

    ``offline`` 曾经也在这里被否决，但它是心跳监控自动写入的观测态：Worker 重启后
    心跳中断即被标记 offline，于是拿不到 Lease，而它必须先拿到 Lease 才会重新上报
    心跳——闭合死锁，真实环境表现为容器无限重启且 lease 恒 409。安全性由两条独立
    机制保证：管理员停用装的是 Redis lifecycle 围栏（LeaseStore.disable_worker），
    回归 Worker 拿到的是新 Lease 代际、旧代际结果会被 fencing token 拒绝。
    """
    worker = SimpleNamespace(public_id="worker-1", status=status)
    with (
        patch.object(Worker, "filter", return_value=_WorkerQuery(worker)),
        patch.object(authority, "has_unacknowledged_v2_registration", AsyncMock(return_value=False)),
    ):
        result = await authority.get_worker_lease_eligibility("worker-1")

    assert result.allowed is False
    assert status in result.reason


@pytest.mark.asyncio
async def test_deleted_and_unacknowledged_workers_are_ineligible() -> None:
    with patch.object(Worker, "filter", return_value=_WorkerQuery(None)):
        missing = await authority.get_worker_lease_eligibility("worker-1")
    worker = SimpleNamespace(public_id="worker-1", status="connecting")
    with (
        patch.object(Worker, "filter", return_value=_WorkerQuery(worker)),
        patch.object(authority, "has_unacknowledged_v2_registration", AsyncMock(return_value=True)),
    ):
        pending = await authority.get_worker_lease_eligibility("worker-1")

    assert missing.allowed is False
    assert pending.allowed is False
    assert "not acknowledged" in pending.reason


@pytest.mark.asyncio
async def test_grant_rechecks_eligibility_and_revokes_issued_generation() -> None:
    store = MagicMock()
    store.grant = AsyncMock(return_value=Lease("worker-1", "lease-1", 10_000, 1_000))
    store.revoke = AsyncMock(return_value=True)
    authorizer = AsyncMock(
        side_effect=[
            authority.WorkerLeaseEligibility("worker-1", True, ""),
            authority.WorkerLeaseEligibility("worker-1", False, "maintenance"),
        ]
    )

    with pytest.raises(WorkerLeaseRejected, match="maintenance"):
        await grant_authorized_worker_lease(
            store, authorizer, WorkerLeaseGrantRequest("worker-1", capabilities=wire_contract_capability())
        )

    store.revoke.assert_awaited_once_with(
        "worker-1",
        reason="authoritative lifecycle changed during grant",
        lease_id="lease-1",
    )


@pytest.mark.asyncio
async def test_rejection_before_grant_does_not_touch_redis() -> None:
    store = MagicMock(grant=AsyncMock(), revoke=AsyncMock())
    authorizer = AsyncMock(return_value=authority.WorkerLeaseEligibility("worker-1", False, "offline"))

    with pytest.raises(WorkerLeaseRejected, match="offline"):
        await grant_authorized_worker_lease(
            store, authorizer, WorkerLeaseGrantRequest("worker-1", capabilities=wire_contract_capability())
        )

    store.grant.assert_not_awaited()
    store.revoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_startup_reconciles_postgres_disabled_and_pending_workers() -> None:
    store = MagicMock()
    store.namespace = "tenant"
    store.disable_worker = AsyncMock(return_value=True)

    with (
        patch.object(Worker, "filter", return_value=_ValuesQuery([("offline-worker", "offline")])),
        patch.object(
            authority.WorkerInstallKey,
            "filter",
            return_value=_ValuesQuery(["pending-worker"]),
        ),
    ):
        await authority.reconcile_worker_lease_lifecycle_fences(store)

    assert store.disable_worker.await_args_list == [
        call("offline-worker", reason="offline", heartbeat_key="{tenant}:heartbeat:offline-worker"),
        call(
            "pending-worker",
            reason="registration-pending",
            heartbeat_key="{tenant}:heartbeat:pending-worker",
        ),
    ]


@pytest.mark.asyncio
async def test_startup_preserves_administrative_reason_over_pending_registration() -> None:
    store = MagicMock(namespace="tenant", disable_worker=AsyncMock(return_value=True))

    with (
        patch.object(Worker, "filter", return_value=_ValuesQuery([("worker-1", "maintenance")])),
        patch.object(authority.WorkerInstallKey, "filter", return_value=_ValuesQuery(["worker-1"])),
    ):
        await authority.reconcile_worker_lease_lifecycle_fences(store)

    store.disable_worker.assert_awaited_once_with(
        "worker-1",
        reason="maintenance",
        heartbeat_key="{tenant}:heartbeat:worker-1",
    )
