from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest
from antcode_contracts import control_pb2
from antcode_gateway.security_audit import EVENT_DEREGISTER_MISSING_GENERATION
from antcode_gateway.services import control_service as control_service_module
from antcode_gateway.services.control_service import GatewayControlService
from antcode_gateway.services.deregister_guard import DEREGISTER_MISSING_GENERATION


class AbortCalled(RuntimeError):
    pass


def _context() -> MagicMock:
    context = MagicMock()
    context.abort = AsyncMock(side_effect=AbortCalled)
    context.peer = MagicMock(return_value="ipv4:10.0.0.9:5555")
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
async def test_deregister_rejects_empty_lease_id(monkeypatch):
    """空 lease_id 必须在触碰 revoke 之前被拒：否则 REVOKE_LUA 无条件 DEL 任意

    worker 的 lease，构成接管活跃 worker 的前置步骤(受控 Redis 已实测)。
    """
    lease_store = MagicMock(revoke=AsyncMock(return_value=True))
    service = GatewayControlService(lease_store=lease_store)
    auditor = MagicMock(emit=AsyncMock())
    service.bind_security_auditor(auditor)
    monkeypatch.setattr(
        control_service_module,
        "require_authenticated_worker",
        AsyncMock(return_value="worker-a"),
    )
    request = control_pb2.DeregisterRequest(worker_id="worker-a", lease_id="", reason="shutdown")
    context = _context()

    with pytest.raises(AbortCalled):
        await service.Deregister(request, context)

    # 摘掉修复(删掉空 lease_id guard)后：revoke 会被调用、不再 abort —— 本用例转红。
    lease_store.revoke.assert_not_awaited()
    context.abort.assert_awaited_once_with(grpc.StatusCode.INVALID_ARGUMENT, DEREGISTER_MISSING_GENERATION)
    emitted = auditor.emit.await_args.args[0]
    assert emitted.event_type == EVENT_DEREGISTER_MISSING_GENERATION
    assert emitted.worker_id == "worker-a"
    assert emitted.peer == "ipv4:10.0.0.9:5555"


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
