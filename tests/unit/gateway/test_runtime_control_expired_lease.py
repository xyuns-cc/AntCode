"""Runtime-control settlement rejects leases that cannot safely commit."""

import grpc
import pytest
from antcode_core.application.services.lease_service import LEASE_RECORD_RETENTION_MS
from antcode_core.infrastructure.redis import control_reply_stream
from antcode_gateway.services.runtime_control_settlement_store import settlement_key

from tests.unit.gateway.test_runtime_control_result import (
    RuntimeResultRedis,
    _context,
    _install,
    _request,
    _service,
)

_ACTIVE_LEASE_TTL_MS = 60_000
_FUTURE_LEASE_EXPIRY_MS = 4_102_444_800_000
_REDIS_TIME_MS = 1_000


class LeaseRejectingRuntimeResultRedis(RuntimeResultRedis):
    def __init__(self) -> None:
        super().__init__()
        self.lease_ttl_ms = _ACTIVE_LEASE_TTL_MS
        self.lease_expires_at_ms = _FUTURE_LEASE_EXPIRY_MS
        self.revoked_leases: set[str] = set()

    async def eval(self, *args):
        if "XPENDING" in args[0]:
            return await super().eval(*args)
        _, _, _, _, _, lease_id, _, _, _, retention_ms = args
        if self._lease_rejected(lease_id, retention_ms):
            self.operations.append("set")
            return [-2, ""]
        return await super().eval(*args)

    def _lease_rejected(self, lease_id: str, retention_ms: int) -> bool:
        return (
            self.lease_ttl_ms <= retention_ms
            or self.lease_expires_at_ms <= _REDIS_TIME_MS
            or lease_id in self.revoked_leases
        )


def _assert_no_settlement_side_effects(redis: RuntimeResultRedis) -> None:
    request = _request()
    assert settlement_key(request.event_id) not in redis.values
    assert (control_reply_stream(request.request_id), "1-0") not in redis.entries
    assert "xack" not in redis.operations


async def _assert_rejected(monkeypatch, redis: RuntimeResultRedis) -> None:
    _install(monkeypatch, redis)
    context = _context()

    response = await _service().AckControl(_request(), context)

    assert response.received is False
    context.abort.assert_awaited_once()
    assert context.abort.await_args.args[0] is grpc.StatusCode.FAILED_PRECONDITION
    _assert_no_settlement_side_effects(redis)


@pytest.mark.asyncio
async def test_runtime_result_rejects_logically_expired_lease_with_high_pttl(monkeypatch):
    redis = LeaseRejectingRuntimeResultRedis()
    redis.lease_ttl_ms = LEASE_RECORD_RETENTION_MS * 10
    redis.lease_expires_at_ms = _REDIS_TIME_MS

    await _assert_rejected(monkeypatch, redis)


@pytest.mark.asyncio
async def test_runtime_result_rejects_revoked_lease(monkeypatch):
    redis = LeaseRejectingRuntimeResultRedis()
    redis.revoked_leases.add(_request().lease_id)

    await _assert_rejected(monkeypatch, redis)


@pytest.mark.asyncio
async def test_runtime_result_rejects_lease_at_retention_boundary(monkeypatch):
    redis = LeaseRejectingRuntimeResultRedis()
    redis.lease_ttl_ms = LEASE_RECORD_RETENTION_MS

    await _assert_rejected(monkeypatch, redis)
