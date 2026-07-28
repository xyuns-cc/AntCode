from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_contracts import control_pb2
from antcode_worker.transport.gateway.transport import GatewayConfig, GatewayTransport


def _response(*, worker_capabilities: bool) -> control_pb2.CapabilitiesResponse:
    return control_pb2.CapabilitiesResponse(
        runtime_control_results_v1=True,
        runtime_control_lease_fencing_v1=True,
        runtime_control_deadline_v1=True,
        artifact_transfer_v1=True,
        runtime_control_authoritative_clock_v1=True,
        worker_capabilities_v1=worker_capabilities,
    )


@pytest.mark.asyncio
async def test_protocol_check_accepts_worker_capabilities_without_registering() -> None:
    stub = MagicMock(GetCapabilities=AsyncMock(return_value=_response(worker_capabilities=True)))
    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="worker-1", api_key="api-key"))
    transport._control_stub = stub

    await transport._check_protocol_capabilities()

    assert stub.GetCapabilities.await_args.args[0].worker_id == "worker-1"


@pytest.mark.asyncio
async def test_protocol_check_requires_worker_capabilities() -> None:
    stub = MagicMock(GetCapabilities=AsyncMock(return_value=_response(worker_capabilities=False)))
    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="worker-1"))
    transport._control_stub = stub

    with pytest.raises(RuntimeError, match="worker_capabilities_v1"):
        await transport._check_protocol_capabilities()
