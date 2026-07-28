"""Gateway runtime-control 权威时钟投递合同。"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_contracts import control_pb2
from antcode_gateway.services import control_service
from antcode_gateway.services.control_service import (
    GatewayControlService,
    _ControlChannel,
    _ControlWatchSession,
)

REDIS_SECONDS = 100
REDIS_MICROSECONDS = 250_000
EXPECTED_REDIS_TIME_MS = 100_250
LIVE_DEADLINE_MS = 130_000


@pytest.mark.asyncio
async def test_gateway_advertises_authoritative_runtime_clock(monkeypatch) -> None:
    authenticate = AsyncMock(return_value="worker-1")
    monkeypatch.setattr(control_service, "require_authenticated_worker", authenticate)
    service = GatewayControlService(lease_store=MagicMock())

    response = await service.GetCapabilities(
        control_pb2.CapabilitiesRequest(worker_id="worker-1"),
        MagicMock(),
    )

    assert response.runtime_control_authoritative_clock_v1 is True


@pytest.mark.asyncio
async def test_runtime_control_delivery_carries_gateway_redis_time() -> None:
    redis = MagicMock(time=AsyncMock(return_value=(REDIS_SECONDS, REDIS_MICROSECONDS)))
    lease_store = MagicMock(is_current=AsyncMock(return_value=True))
    service = GatewayControlService(lease_store=lease_store)
    service._settle_recovered_control = AsyncMock(return_value=False)
    session = _session()

    event = await service._decode_control_event(
        redis,
        session,
        stream_key="antcode:control:worker-1",
        message_id="1-0",
        data=_runtime_event_data(),
    )

    assert event is not None
    assert event.runtime_control.gateway_observed_at_ms == EXPECTED_REDIS_TIME_MS


def _session() -> _ControlWatchSession:
    return _ControlWatchSession(
        context=MagicMock(),
        worker_id="worker-1",
        lease_id="lease-1",
        consumer="worker-1",
        channels=(_ControlChannel("antcode:control:worker-1", "antcode-control"),),
    )


def _runtime_event_data() -> dict[str, str]:
    return {
        "control_type": "runtime_manage",
        "request_id": "request-1",
        "action": "list_envs",
        "reply_stream": "antcode:control:reply:request-1",
        "payload": "{}",
        "expires_at_ms": str(LIVE_DEADLINE_MS),
    }
