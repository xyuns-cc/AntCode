from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest
from antcode_contracts import control_pb2
from antcode_contracts.transcode import encode_capabilities
from antcode_contracts.wire_contract import wire_contract_capability
from antcode_core.application.services.lease_service import LeaseIneligibleError
from antcode_core.application.services.workers.worker_lease_authority import WorkerLeaseEligibility
from antcode_gateway.services import control_service as control_service_module
from antcode_gateway.services.control_service import GatewayControlService

RENEW_AFTER_MS = 10_000
AUTHORITY_CHECKS_PER_GRANT = 2


class AbortCalled(RuntimeError):
    pass


def _lease_request() -> control_pb2.LeaseRequest:
    """本文件测的是生命周期权威，线协议契约一律给当前版本，否则请求在更早一层被拒。"""
    return control_pb2.LeaseRequest(
        worker_id="worker-1",
        capabilities=encode_capabilities(wire_contract_capability()),
    )


def _context() -> MagicMock:
    return MagicMock(abort=AsyncMock(side_effect=AbortCalled))


def _store(*, lease: object | None = None) -> MagicMock:
    granted = lease or SimpleNamespace(worker_id="worker-1", lease_id="lease-1", expires_at_ms=30_000)
    return MagicMock(
        policy=SimpleNamespace(ttl_ms=30_000, renew_after_ms=RENEW_AFTER_MS),
        grant=AsyncMock(return_value=granted),
        revoke=AsyncMock(return_value=True),
    )


def _authorizer(*results: WorkerLeaseEligibility | Exception) -> AsyncMock:
    return AsyncMock(side_effect=list(results))


def _eligibility(allowed: bool, reason: str = "") -> WorkerLeaseEligibility:
    return WorkerLeaseEligibility("worker-1", allowed, reason)


def _service(monkeypatch, store: MagicMock, authorizer: AsyncMock) -> GatewayControlService:
    monkeypatch.setattr(
        control_service_module,
        "require_authenticated_worker",
        AsyncMock(return_value="worker-1"),
    )
    return GatewayControlService(lease_store=store, lease_authorizer=authorizer)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason",
    [
        "Worker does not exist",
        "Worker status does not allow leases: maintenance",
        "Worker status does not allow leases: offline",
        "Worker V2 registration is not acknowledged",
    ],
)
async def test_lease_rejects_ineligible_worker_before_grant(monkeypatch, reason: str) -> None:
    store = _store()
    service = _service(monkeypatch, store, _authorizer(_eligibility(False, reason)))

    response = await service.Lease(_lease_request(), _context())

    assert response.revoked is True
    assert response.revoke_reason == reason
    store.grant.assert_not_awaited()


@pytest.mark.asyncio
async def test_acknowledged_connecting_worker_can_receive_first_lease(monkeypatch) -> None:
    store = _store()
    authorizer = _authorizer(_eligibility(True), _eligibility(True))
    service = _service(monkeypatch, store, authorizer)

    response = await service.Lease(_lease_request(), _context())

    assert response.lease_id == "lease-1"
    assert response.revoked is False
    assert authorizer.await_count == AUTHORITY_CHECKS_PER_GRANT
    store.revoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_lease_authority_failure_is_unavailable_and_does_not_grant(monkeypatch) -> None:
    store = _store()
    service = _service(monkeypatch, store, _authorizer(RuntimeError("postgres unavailable")))
    context = _context()

    with pytest.raises(AbortCalled):
        await service.Lease(_lease_request(), context)

    assert context.abort.await_args.args[0] is grpc.StatusCode.UNAVAILABLE
    store.grant.assert_not_awaited()


@pytest.mark.asyncio
async def test_atomic_lifecycle_fence_is_returned_as_revoked(monkeypatch) -> None:
    store = _store()
    store.grant.side_effect = LeaseIneligibleError("worker-1")
    service = _service(monkeypatch, store, _authorizer(_eligibility(True)))

    response = await service.Lease(_lease_request(), _context())

    assert response.revoked is True
    assert "lifecycle" in response.revoke_reason


@pytest.mark.asyncio
async def test_post_grant_ineligibility_revokes_exact_generation(monkeypatch) -> None:
    store = _store()
    authorizer = _authorizer(_eligibility(True), _eligibility(False, "Worker deleted during grant"))
    service = _service(monkeypatch, store, authorizer)

    response = await service.Lease(_lease_request(), _context())

    assert response.revoked is True
    assert response.revoke_reason == "Worker deleted during grant"
    store.revoke.assert_awaited_once_with(
        "worker-1",
        reason="authoritative lifecycle changed during grant",
        lease_id="lease-1",
    )


@pytest.mark.asyncio
async def test_post_grant_authority_failure_revokes_before_unavailable(monkeypatch) -> None:
    store = _store()
    authorizer = _authorizer(_eligibility(True), RuntimeError("postgres unavailable"))
    service = _service(monkeypatch, store, authorizer)
    context = _context()

    with pytest.raises(AbortCalled):
        await service.Lease(_lease_request(), context)

    store.revoke.assert_awaited_once_with(
        "worker-1",
        reason="authoritative lifecycle changed during grant",
        lease_id="lease-1",
    )
    assert context.abort.await_args.args[0] is grpc.StatusCode.UNAVAILABLE


@pytest.mark.asyncio
async def test_missing_lease_authorizer_fails_closed(monkeypatch) -> None:
    store = _store()
    monkeypatch.setattr(
        control_service_module,
        "require_authenticated_worker",
        AsyncMock(return_value="worker-1"),
    )
    service = GatewayControlService(lease_store=store)
    context = _context()

    with pytest.raises(AbortCalled):
        await service.Lease(_lease_request(), context)

    assert context.abort.await_args.args[0] is grpc.StatusCode.UNAVAILABLE
    store.grant.assert_not_awaited()
