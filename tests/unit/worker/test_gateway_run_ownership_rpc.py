from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import antcode_core.infrastructure.redis as redis_module
import pytest
from antcode_contracts import artifact_pb2
from antcode_worker.engine.engine import Engine
from antcode_worker.transport.base import TransportMode
from antcode_worker.transport.gateway.transport import GatewayConfig, GatewayTransport


def _gateway_engine() -> tuple[Engine, SimpleNamespace]:
    transport = SimpleNamespace(
        mode=TransportMode.GATEWAY,
        claim_run_ownership=AsyncMock(return_value=True),
        renew_run_ownership=AsyncMock(return_value=True),
        release_run_ownership=AsyncMock(return_value=True),
    )
    engine = Engine.__new__(Engine)
    engine._transport = transport
    return engine, transport


@pytest.mark.asyncio
async def test_engine_gateway_ownership_never_requests_redis(monkeypatch):
    get_redis = AsyncMock()
    monkeypatch.setattr(redis_module, "get_redis_client", get_redis)
    engine, transport = _gateway_engine()
    ttl_ms = Engine._RUN_OWNERSHIP_TTL_SECONDS * 1000

    assert await engine._claim_run_ownership("run-1") is True
    assert await engine._renew_run_ownership("run-1") is True
    await engine._release_run_ownership("run-1")

    transport.claim_run_ownership.assert_awaited_once_with("run-1", ttl_ms)
    transport.renew_run_ownership.assert_awaited_once_with("run-1", ttl_ms)
    transport.release_run_ownership.assert_awaited_once_with("run-1")
    get_redis.assert_not_awaited()


@pytest.mark.asyncio
async def test_gateway_transport_sends_authenticated_ownership_requests():
    stub = MagicMock(
        ClaimRunOwnership=AsyncMock(return_value=artifact_pb2.RunOwnershipClaimResponse(acquired=True)),
        RenewRunOwnership=AsyncMock(return_value=artifact_pb2.RunOwnershipRenewResponse(renewed=True)),
        ReleaseRunOwnership=AsyncMock(return_value=artifact_pb2.RunOwnershipReleaseResponse(released=True)),
    )
    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="worker-1"))
    transport._running = True
    transport._lease_id = "lease-1"
    transport._artifact_stub = stub

    assert await transport.claim_run_ownership("run-1", 3_900_000) is True
    assert await transport.renew_run_ownership("run-1", 3_900_000) is True
    assert await transport.release_run_ownership("run-1") is True

    claim = stub.ClaimRunOwnership.await_args.args[0]
    assert (claim.worker_id, claim.lease_id, claim.run_id, claim.ttl_ms) == (
        "worker-1",
        "lease-1",
        "run-1",
        3_900_000,
    )
    assert stub.RenewRunOwnership.await_args.args[0].lease_id == "lease-1"
    assert stub.ReleaseRunOwnership.await_args.args[0].run_id == "run-1"


def test_artifact_rpc_session_requires_live_stub_and_lease():
    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="worker-1"))
    with pytest.raises(RuntimeError, match="stub 未初始化"):
        transport.artifact_rpc_session()

    stub = MagicMock()
    transport._running = True
    transport._artifact_stub = stub
    transport._lease_id = "lease-1"

    assert transport.artifact_rpc_session() == (stub, "worker-1", "lease-1", ())
