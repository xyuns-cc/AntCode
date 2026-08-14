from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_core.application.services.workers.worker_lease_lifecycle import WorkerLeaseLifecycleFence


@pytest.mark.asyncio
async def test_disable_fences_before_revoke_and_heartbeat_delete() -> None:
    redis = MagicMock()
    store = MagicMock(namespace="tenant")
    store.disable_worker = AsyncMock(return_value=True)

    revoked = await WorkerLeaseLifecycleFence(redis, store).disable("worker-1", "maintenance")

    assert revoked is True
    store.disable_worker.assert_awaited_once_with(
        "worker-1",
        reason="maintenance",
        heartbeat_key="{tenant}:heartbeat:worker-1",
    )


@pytest.mark.asyncio
async def test_disable_exposes_revoke_failure_without_clearing_heartbeat() -> None:
    redis = MagicMock()
    store = MagicMock(namespace="tenant")
    store.disable_worker = AsyncMock(side_effect=RuntimeError("redis revoke failed"))

    with pytest.raises(RuntimeError, match="redis revoke failed"):
        await WorkerLeaseLifecycleFence(redis, store).disable("worker-1", "offline")

    store.disable_worker.assert_awaited_once()
