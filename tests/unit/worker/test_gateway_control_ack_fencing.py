from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_contracts import control_pb2
from antcode_worker.transport.gateway.transport import GatewayConfig, GatewayTransport


@pytest.mark.asyncio
async def test_gateway_control_ack_false_is_exposed_to_caller():
    stub = MagicMock(
        AckControl=AsyncMock(
            return_value=control_pb2.AckControlResponse(received=False),
        )
    )
    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="worker-1"))
    transport._control_stub = stub
    transport._lease_id = "lease-1"
    transport._running = True

    received = await transport.ack_control("antcode:control:worker-1|1-0")

    assert received is False
    assert stub.AckControl.await_args.args[0].lease_id == "lease-1"
