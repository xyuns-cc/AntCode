from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_core.application.services.lease_service import LeaseStore


@pytest.mark.asyncio
async def test_grant_updates_primary_record_and_indexes_in_one_script():
    redis = MagicMock()
    store = LeaseStore(redis, namespace="tenant")
    store._evalsha_grant = AsyncMock(return_value=["lease-id", "2000", "1000", "new"])

    lease = await store.grant("worker-a")

    keys = store._evalsha_grant.await_args.args[0]
    assert keys == [
        "{tenant}:lease:data:worker-a",
        "{tenant}:lease:revoked:worker-a",
        "{tenant}:lease:expiring",
        "{tenant}:lease:active",
    ]
    assert lease.lease_id == "lease-id"
    redis.pipeline.assert_not_called()


@pytest.mark.asyncio
async def test_revoke_updates_primary_record_and_indexes_in_one_script():
    redis = MagicMock()
    store = LeaseStore(redis, namespace="tenant")
    store._evalsha_revoke = AsyncMock(return_value=1)

    assert await store.revoke("worker-a", lease_id="lease-id") is True

    keys = store._evalsha_revoke.await_args.args[0]
    assert keys[-2:] == [
        "{tenant}:lease:expiring",
        "{tenant}:lease:active",
    ]
    redis.pipeline.assert_not_called()
