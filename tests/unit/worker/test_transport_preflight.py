"""Transport preflight checks must be read-only and capability complete."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_contracts import control_pb2, control_pb2_grpc
from antcode_worker.transport.factory import (
    DirectConfig,
    GatewayConfigSpec,
    TransportConfig,
    _check_gateway_capabilities,
    preflight_check_direct,
)


@pytest.mark.asyncio
async def test_direct_preflight_inspects_group_without_claiming_tasks(monkeypatch):
    redis = AsyncMock()
    redis.xgroup_create.side_effect = RuntimeError("BUSYGROUP Consumer Group name already exists")
    redis.xinfo_groups.return_value = [{"name": "antcode-workers"}]
    monkeypatch.setattr(
        "antcode_core.infrastructure.redis.factory.create_async_redis_client",
        lambda *_args, **_kwargs: redis,
    )
    config = TransportConfig(
        mode="direct",
        worker_id="worker-001",
        direct=DirectConfig(redis_url="redis://10.0.0.10:6379/0"),
    )

    assert await preflight_check_direct(config) is True

    redis.xinfo_groups.assert_awaited_once_with("{antcode}:task:ready:worker-001")
    redis.xreadgroup.assert_not_called()
    redis.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_gateway_preflight_requires_artifact_transfer(monkeypatch):
    response = control_pb2.CapabilitiesResponse(
        runtime_control_results_v1=True,
        runtime_control_lease_fencing_v1=True,
        runtime_control_deadline_v1=True,
        artifact_transfer_v1=False,
    )
    stub = MagicMock(GetCapabilities=AsyncMock(return_value=response))
    monkeypatch.setattr(control_pb2_grpc, "ControlServiceStub", lambda _channel: stub)
    config = TransportConfig(
        mode="gateway",
        worker_id="worker-001",
        gateway=GatewayConfigSpec(host="gateway.example.com", api_key="key"),
    )

    with pytest.raises(RuntimeError, match="artifact_transfer_v1"):
        await _check_gateway_capabilities(MagicMock(), config)
