from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_contracts import common_pb2, control_pb2
from antcode_gateway.services import control_service as control_service_module
from antcode_gateway.services.control_service import GatewayControlService
from antcode_gateway.services.lease_heartbeat import decode_lease_heartbeat


def _request(capabilities: dict[str, str]) -> control_pb2.LeaseRequest:
    return control_pb2.LeaseRequest(
        worker_id="worker-1",
        metrics=common_pb2.Metrics(max_concurrent_tasks=8),
        capabilities=capabilities,
    )


def test_lease_capabilities_decode_to_structured_values() -> None:
    payload = decode_lease_heartbeat(
        _request({"task_types": '["code","rule"]', "curl_cffi": '{"enabled":true}'}),
        "worker-1",
    )

    assert payload.capabilities == {
        "task_types": ["code", "rule"],
        "curl_cffi": {"enabled": True},
    }
    assert payload.heartbeat is not None
    assert payload.heartbeat.capabilities == payload.capabilities


def test_lease_capabilities_reject_invalid_json() -> None:
    with pytest.raises(ValueError):
        decode_lease_heartbeat(_request({"task_types": "not-json"}), "worker-1")


@pytest.mark.asyncio
async def test_lease_grant_is_authoritative_when_legacy_view_fails(monkeypatch) -> None:
    lease = SimpleNamespace(lease_id="lease-1", expires_at_ms=30_000)
    store = MagicMock(
        policy=SimpleNamespace(ttl_ms=30_000, renew_after_ms=10_000),
        grant=AsyncMock(return_value=lease),
    )
    handler = MagicMock(handle=AsyncMock(return_value=False))
    service = GatewayControlService(lease_handler=handler, lease_store=store)
    monkeypatch.setattr(
        control_service_module,
        "require_authenticated_worker",
        AsyncMock(return_value="worker-1"),
    )

    response = await service.Lease(_request({"task_types": '["code","rule"]'}), MagicMock())

    assert response.lease_id == "lease-1"
    store.grant.assert_awaited_once_with(
        "worker-1",
        current_lease_id="",
        metrics={
            "cpu": 0.0,
            "memory": 0.0,
            "disk": 0.0,
            "running_tasks": 0,
            "max_concurrent_tasks": 8,
        },
        capabilities={"task_types": ["code", "rule"]},
    )
    store.revoke.assert_not_called()
