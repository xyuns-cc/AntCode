"""Direct Lease capability snapshot regression tests.

Direct 控制面此前用 ``workers.capabilities``（心跳链路异步写入的投影）签发
Lease。首次签发时该列还是空的，心跳落库后的下一次续租就会被 ``GRANT_LUA``
判 ``capabilities_changed`` → 撤销代际 → Worker 在任务执行中途自我停机。
这里锁定修复后的合同：能力快照由 Worker 随每次 Lease 调用带上，且逐次一致。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_worker.transport.redis.direct_control import DirectLeaseGrant
from antcode_worker.transport.redis.transport import RedisTransport

STARTUP_CAPABILITIES = {"task_types": ["code", "rule"], "playwright": {"enabled": True}}
WIRE_CAPABILITIES = {"task_types": '["code","rule"]', "playwright": '{"enabled":true}'}
EXPECTED_RENEW_CALLS = 2


def _transport(lease_id: str) -> RedisTransport:
    control = SimpleNamespace(
        lease_renew=AsyncMock(
            return_value=DirectLeaseGrant(
                lease_id=lease_id,
                expires_at_ms=123,
                renew_after_ms=10_000,
                ttl_ms=30_000,
                revoked=False,
            )
        )
    )
    transport = RedisTransport(
        redis_url="redis://localhost:6379/0",
        worker_id="worker-1",
        direct_control=control,
    )
    transport._lease_store = SimpleNamespace(policy=SimpleNamespace(renew_after_ms=10_000))
    return transport


@pytest.mark.asyncio
async def test_direct_lease_carries_the_startup_capability_snapshot() -> None:
    transport = _transport("lease-1")
    transport.set_capabilities(STARTUP_CAPABILITIES)

    await transport.lease_renew("")

    transport._direct_control.lease_renew.assert_awaited_once_with("", None, WIRE_CAPABILITIES)


@pytest.mark.asyncio
async def test_direct_renew_repeats_the_same_capability_snapshot() -> None:
    transport = _transport("lease-1")
    transport.set_capabilities(STARTUP_CAPABILITIES)

    await transport.lease_renew("")
    await transport.lease_renew("lease-1")

    calls = transport._direct_control.lease_renew.await_args_list
    assert len(calls) == EXPECTED_RENEW_CALLS
    assert calls[0].args[2] == calls[1].args[2] == WIRE_CAPABILITIES


def test_direct_transport_rejects_capability_values_that_are_not_wire_encodable() -> None:
    transport = _transport("lease-1")

    with pytest.raises(ValueError, match="finite standard JSON"):
        transport.set_capabilities({"broken": float("nan")})
