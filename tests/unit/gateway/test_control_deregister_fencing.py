from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest
from antcode_contracts import control_pb2
from antcode_gateway.services import control_service as control_service_module
from antcode_gateway.services.control_service import GatewayControlService


class AbortCalled(RuntimeError):
    pass


def _context() -> MagicMock:
    context = MagicMock()
    context.abort = AsyncMock(side_effect=AbortCalled)
    return context


@pytest.mark.asyncio
async def test_deregister_rejects_stale_lease(monkeypatch):
    lease_store = MagicMock(
        revoke=AsyncMock(return_value=False),
    )
    service = GatewayControlService(lease_store=lease_store)
    monkeypatch.setattr(
        control_service_module,
        "require_authenticated_worker",
        AsyncMock(return_value="worker-a"),
    )
    request = control_pb2.DeregisterRequest(
        worker_id="worker-a",
        lease_id="stale-lease",
        reason="shutdown",
    )

    with pytest.raises(AbortCalled):
        await service.Deregister(request, _context())

    lease_store.revoke.assert_awaited_once_with(
        "worker-a",
        reason="deregister:shutdown",
        lease_id="stale-lease",
    )


@pytest.mark.asyncio
async def test_deregister_revokes_current_lease(monkeypatch):
    lease_store = MagicMock(
        revoke=AsyncMock(return_value=True),
    )
    service = GatewayControlService(lease_store=lease_store)
    monkeypatch.setattr(
        control_service_module,
        "require_authenticated_worker",
        AsyncMock(return_value="worker-a"),
    )
    monkeypatch.setattr(
        control_service_module,
        "get_redis_client",
        AsyncMock(return_value=None),
    )
    request = control_pb2.DeregisterRequest(
        worker_id="worker-a",
        lease_id="lease-a",
        reason="shutdown",
    )

    response = await service.Deregister(request, _context())

    assert response.success is True
    lease_store.revoke.assert_awaited_once_with(
        "worker-a",
        reason="deregister:shutdown",
        lease_id="lease-a",
    )
