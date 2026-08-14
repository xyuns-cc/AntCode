from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_worker.heartbeat.reporter import HeartbeatReporter
from antcode_worker.transport.base import GenerationLostError
from antcode_worker.transport.redis.direct_control import DirectLeaseGrant
from antcode_worker.transport.redis.transport import RedisTransport


def _transport() -> RedisTransport:
    control = SimpleNamespace(
        lease_renew=AsyncMock(
            return_value=DirectLeaseGrant(lease_id="", expires_at_ms=0, renew_after_ms=0, ttl_ms=0, revoked=True)
        )
    )
    transport = RedisTransport(
        redis_url="redis://localhost:6379/0",
        worker_id="worker-1",
        direct_control=control,
    )
    transport._lease_store = MagicMock()
    transport._lease_fencing_enabled = True
    transport._lease_id = "lease-1"
    return transport


@pytest.mark.asyncio
async def test_direct_revocation_notifies_engine_and_fails_generation() -> None:
    transport = _transport()
    callback = AsyncMock()
    transport.set_lease_revoked_callback(callback)

    with pytest.raises(GenerationLostError, match="revoked"):
        await transport.lease_renew("lease-1")

    callback.assert_awaited_once()
    assert transport._generation_lost is True


@pytest.mark.asyncio
async def test_local_generation_loss_rejects_without_redis_query() -> None:
    transport = _transport()
    transport._generation_lost = True
    transport._lease_store.is_current = AsyncMock()

    with pytest.raises(GenerationLostError):
        await transport._require_current_generation()

    transport._lease_store.is_current.assert_not_awaited()


@pytest.mark.asyncio
async def test_heartbeat_reporter_propagates_generation_loss() -> None:
    transport = MagicMock(is_connected=True)
    transport.send_heartbeat = AsyncMock(side_effect=GenerationLostError("superseded"))
    reporter = HeartbeatReporter(transport=transport, worker_id="worker-1")

    with pytest.raises(GenerationLostError, match="superseded"):
        await reporter.send_heartbeat()
