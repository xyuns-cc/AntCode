"""Lifecycle and capability mutation contracts for LeaseStore."""

from __future__ import annotations

import secrets

import pytest
import pytest_asyncio
from antcode_core.application.services.lease_service import LeaseIneligibleError, LeaseRevokedError, LeaseStore

from tests.contracts.lease_fixtures import lease_store_fixture, redis_client_fixture

redis_client = pytest_asyncio.fixture(redis_client_fixture)
lease_store = pytest_asyncio.fixture(lease_store_fixture)


@pytest.mark.asyncio
async def test_renew_rejects_capability_change_without_mutating_generation(lease_store: LeaseStore, redis_client):
    worker_id = f"worker-{secrets.token_hex(3)}"
    first = await lease_store.grant(worker_id, capabilities={"task_types": ["code"]})

    with pytest.raises(LeaseRevokedError, match="新代际"):
        await lease_store.grant(
            worker_id,
            current_lease_id=first.lease_id,
            capabilities={"task_types": ["rule"]},
        )

    assert await lease_store.get(worker_id) == first
    assert await redis_client.hget(lease_store.lease_key(worker_id), "capabilities_json") == '{"task_types":["code"]}'


@pytest.mark.asyncio
async def test_lifecycle_fence_rejects_first_grant_and_renewal(lease_store: LeaseStore, redis_client):
    worker_id = f"worker-{secrets.token_hex(3)}"
    first = await lease_store.grant(worker_id)
    await redis_client.set(lease_store.lifecycle_key(worker_id), "maintenance")

    with pytest.raises(LeaseIneligibleError):
        await lease_store.grant(worker_id, current_lease_id=first.lease_id)
    await lease_store.revoke(worker_id, reason="maintenance")
    with pytest.raises(LeaseIneligibleError):
        await lease_store.grant(worker_id)


@pytest.mark.asyncio
async def test_enable_worker_compare_deletes_only_expected_reason(lease_store: LeaseStore, redis_client):
    worker_id = f"worker-{secrets.token_hex(3)}"
    lifecycle_key = lease_store.lifecycle_key(worker_id)
    await redis_client.set(lifecycle_key, "maintenance")

    with pytest.raises(LeaseIneligibleError):
        await lease_store.enable_worker(worker_id, expected_reasons=("registration-pending",))
    assert await redis_client.get(lifecycle_key) == "maintenance"
    assert (
        await lease_store.enable_worker(
            worker_id,
            expected_reasons=("registration-pending",),
            allow_mismatch=True,
        )
        is False
    )
    assert await lease_store.enable_worker(worker_id, expected_reasons=("maintenance", "disconnect")) is True
    assert await redis_client.exists(lifecycle_key) == 0
