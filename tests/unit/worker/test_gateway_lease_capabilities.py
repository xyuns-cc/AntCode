import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_contracts import control_pb2
from antcode_worker.transport.gateway.transport import GatewayConfig, GatewayTransport


@pytest.mark.asyncio
async def test_initial_lease_uses_the_startup_capability_snapshot() -> None:
    response = control_pb2.LeaseResponse(
        lease_id="lease-1",
        expires_at_ms=int(time.time() * 1000) + 30_000,
        renew_after_ms=10_000,
    )
    stub = MagicMock(Lease=AsyncMock(return_value=response))
    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="worker-1"))
    transport._running = True
    transport._control_stub = stub
    transport.set_capabilities({"task_types": ["code", "rule"]})

    lease_id, _expires, _renew, revoked = await transport.lease_renew("")

    request = stub.Lease.await_args.args[0]
    assert lease_id == "lease-1"
    assert revoked is False
    assert json.loads(request.capabilities["task_types"]) == ["code", "rule"]
