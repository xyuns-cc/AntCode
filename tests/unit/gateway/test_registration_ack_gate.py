from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_contracts import control_pb2
from antcode_core.domain.models import WorkerStatus
from antcode_gateway.services import registration_gate
from antcode_gateway.services.control_service import GatewayControlService

LEASE_TTL_MS = 30_000
LEASE_RENEW_AFTER_MS = 10_000


def _context() -> MagicMock:
    context = MagicMock()
    context.cancelled.return_value = False
    return context


def _service(lease_store: MagicMock) -> GatewayControlService:
    return GatewayControlService(lease_store=lease_store)


@pytest.mark.asyncio
async def test_register_does_not_create_an_unusable_lease(monkeypatch) -> None:
    lease_store = MagicMock()
    lease_store.policy.ttl_ms = LEASE_TTL_MS
    lease_store.policy.renew_after_ms = LEASE_RENEW_AFTER_MS
    service = _service(lease_store)
    monkeypatch.setattr(
        "antcode_gateway.services.control_service.registration_validation_error",
        AsyncMock(return_value=None),
    )
    request = control_pb2.RegisterRequest(worker_id="worker-a", api_key="api-key")

    response = await service.Register(request, _context())

    assert response.success is True
    assert response.lease_ttl_ms == LEASE_TTL_MS
    lease_store.grant.assert_not_called()


@pytest.mark.asyncio
async def test_register_rejects_unacknowledged_v2_worker(monkeypatch) -> None:
    lease_store = MagicMock()
    service = _service(lease_store)
    monkeypatch.setattr(
        "antcode_gateway.services.control_service.registration_validation_error",
        AsyncMock(return_value="Worker V2 registration is not acknowledged"),
    )
    request = control_pb2.RegisterRequest(worker_id="worker-a", api_key="api-key")

    response = await service.Register(request, _context())

    assert response.success is False
    assert "not acknowledged" in response.error
    lease_store.grant.assert_not_called()


@pytest.mark.asyncio
async def test_registration_validation_rejects_retired_worker(monkeypatch) -> None:
    worker = MagicMock(public_id="worker-a", status=WorkerStatus.MAINTENANCE.value)
    query = MagicMock()
    query.only.return_value.first = AsyncMock(return_value=worker)
    monkeypatch.setattr(registration_gate, "verify_api_key", AsyncMock(return_value=True))
    monkeypatch.setattr(registration_gate.Worker, "filter", MagicMock(return_value=query))
    readiness = AsyncMock()
    monkeypatch.setattr(registration_gate, "registration_rejection", readiness)

    error = await registration_gate.registration_validation_error("api-key", "worker-a")

    assert "状态不允许注册" in (error or "")
    readiness.assert_not_awaited()
