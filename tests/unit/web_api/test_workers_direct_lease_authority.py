"""Direct Lease authority, metrics, and capability tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_contracts.capabilities import MAX_CAPABILITY_VALUE_BYTES
from antcode_contracts.wire_contract import WIRE_CONTRACT_CAPABILITY, WORKER_WIRE_CONTRACT_VERSION
from antcode_core.application.services.lease_service import Lease, LeaseIneligibleError
from antcode_core.application.services.workers.worker_lease_authority import WorkerLeaseEligibility
from antcode_web_api.routes.v1 import workers_direct_control as module
from antcode_web_api.routes.v1 import workers_direct_lease as lease_module
from fastapi import HTTPException
from pydantic import ValidationError

EXPECTED_TASK_COUNT = 12
EXPECTED_PROJECT_COUNT = 3
EXPECTED_ENV_COUNT = 4
LEASE_EXPIRES_AT_MS = 100_000
LEASE_GRANTED_AT_MS = 100
EXPECTED_AUTHORITY_CHECKS = 2
HTTP_BAD_REQUEST = 400
HTTP_CONFLICT = 409
HTTP_SERVICE_UNAVAILABLE = 503
# 控制面在签发前校验 wire_contract，缺失即拒发，因此每个快照都必须带上它。
WIRE_CAPABILITIES = {"task_types": '["code"]', WIRE_CONTRACT_CAPABILITY: str(WORKER_WIRE_CONTRACT_VERSION)}
DECODED_CAPABILITIES = {"task_types": ["code"], WIRE_CONTRACT_CAPABILITY: WORKER_WIRE_CONTRACT_VERSION}


def _worker(capabilities: dict | None = None):
    return SimpleNamespace(
        public_id="worker-1",
        transport_mode="direct",
        capabilities=DECODED_CAPABILITIES if capabilities is None else capabilities,
        id=1,
        save=AsyncMock(),
    )


def _lease_request(capabilities: dict[str, str] | None = None):
    return module.DirectLeaseRequest(
        operation="lease",
        capabilities=WIRE_CAPABILITIES if capabilities is None else capabilities,
    )


def test_direct_lease_accepts_and_normalizes_extended_metrics() -> None:
    request = module.DirectLeaseRequest(
        operation="lease",
        capabilities=WIRE_CAPABILITIES,
        metrics={
            "task_count": EXPECTED_TASK_COUNT,
            "project_count": EXPECTED_PROJECT_COUNT,
            "env_count": EXPECTED_ENV_COUNT,
            "spider_stats": {
                "request_count": 7,
                "status_codes": {"0200": 7},
                "domain_stats": [{"domain": "example.com", "reqs": 7, "successRate": 100, "latency": 4.5}],
            },
        },
    )
    metrics = request.metrics.model_dump() if request.metrics else {}
    assert metrics["task_count"] == EXPECTED_TASK_COUNT
    assert metrics["project_count"] == EXPECTED_PROJECT_COUNT
    assert metrics["env_count"] == EXPECTED_ENV_COUNT
    assert metrics["spider_stats"]["status_codes"] == {"200": 7}


@pytest.mark.parametrize(
    "metrics",
    [
        {"task_count": -1},
        {"spider_stats": {"avg_latency_ms": float("nan")}},
        {"spider_stats": {"status_codes": {"99": 1}}},
        {"spider_stats": {"status_codes": {"invalid": 1}}},
    ],
)
def test_direct_lease_rejects_poisoned_extended_metrics(metrics) -> None:
    with pytest.raises(ValidationError):
        module.DirectLeaseRequest(operation="lease", capabilities=WIRE_CAPABILITIES, metrics=metrics)


def test_direct_lease_requires_a_worker_supplied_capability_snapshot() -> None:
    # 控制面不得自己代填能力：漏带字段必须 422，而不是退化成空快照签发。
    with pytest.raises(ValidationError):
        module.DirectLeaseRequest(operation="lease")


def _lease_store(grant: AsyncMock) -> SimpleNamespace:
    return SimpleNamespace(policy=SimpleNamespace(renew_after_ms=10_000, ttl_ms=30_000), grant=grant)


@pytest.mark.asyncio
async def test_direct_lease_uses_the_request_capability_snapshot(monkeypatch) -> None:
    lease = Lease("worker-1", "lease-1", LEASE_EXPIRES_AT_MS, LEASE_GRANTED_AT_MS)
    grant = AsyncMock(return_value=lease)
    store = _lease_store(grant)
    authorize = AsyncMock(return_value=WorkerLeaseEligibility("worker-1", True, ""))
    monkeypatch.setattr(module, "_redis_client", AsyncMock(return_value=object()))
    monkeypatch.setattr(lease_module, "get_worker_lease_eligibility", authorize)
    monkeypatch.setattr(lease_module, "LeaseStore", lambda *_args, **_kwargs: store)
    # 数据库里的旧快照必须被忽略：它由心跳链路异步写入，早于/晚于签发都可能。
    worker = _worker(capabilities={"task_types": ["rule"], WIRE_CONTRACT_CAPABILITY: WORKER_WIRE_CONTRACT_VERSION})

    response = await module._grant_direct_lease(worker, _lease_request())

    assert response.data["lease_id"] == "lease-1"
    assert grant.await_args.kwargs["capabilities"] == DECODED_CAPABILITIES
    assert authorize.await_count == EXPECTED_AUTHORITY_CHECKS
    assert worker.capabilities == DECODED_CAPABILITIES
    worker.save.assert_awaited_once_with(update_fields=["capabilities"])


@pytest.mark.asyncio
async def test_direct_lease_leaves_matching_projection_untouched(monkeypatch) -> None:
    lease = Lease("worker-1", "lease-1", LEASE_EXPIRES_AT_MS, LEASE_GRANTED_AT_MS)
    store = _lease_store(AsyncMock(return_value=lease))
    monkeypatch.setattr(module, "_redis_client", AsyncMock(return_value=object()))
    monkeypatch.setattr(
        lease_module,
        "get_worker_lease_eligibility",
        AsyncMock(return_value=WorkerLeaseEligibility("worker-1", True, "")),
    )
    monkeypatch.setattr(lease_module, "LeaseStore", lambda *_args, **_kwargs: store)
    worker = _worker()

    await module._grant_direct_lease(worker, _lease_request())

    worker.save.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_lease_rejects_oversized_request_capability(monkeypatch) -> None:
    grant = AsyncMock()
    store = _lease_store(grant)
    monkeypatch.setattr(module, "_redis_client", AsyncMock(return_value=object()))
    monkeypatch.setattr(lease_module, "LeaseStore", lambda *_args, **_kwargs: store)
    oversized = {"oversized": '"{}"'.format("x" * MAX_CAPABILITY_VALUE_BYTES)}

    with pytest.raises(HTTPException) as caught:
        await module._grant_direct_lease(_worker(), _lease_request(oversized))

    assert caught.value.status_code == HTTP_BAD_REQUEST
    grant.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["offline", "maintenance", "registration pending"])
async def test_direct_lease_rejects_authoritative_lifecycle_before_grant(monkeypatch, reason: str) -> None:
    store = SimpleNamespace(policy=SimpleNamespace(renew_after_ms=10_000, ttl_ms=30_000), grant=AsyncMock())
    authorize = AsyncMock(return_value=WorkerLeaseEligibility("worker-1", False, reason))
    monkeypatch.setattr(module, "_redis_client", AsyncMock(return_value=object()))
    monkeypatch.setattr(lease_module, "get_worker_lease_eligibility", authorize)
    monkeypatch.setattr(lease_module, "LeaseStore", lambda *_args, **_kwargs: store)

    with pytest.raises(HTTPException) as caught:
        await module._grant_direct_lease(_worker(), _lease_request())

    assert caught.value.status_code == HTTP_CONFLICT
    assert caught.value.detail == reason
    store.grant.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_lease_maps_atomic_lifecycle_fence_to_revoked_response(monkeypatch) -> None:
    store = SimpleNamespace(
        policy=SimpleNamespace(renew_after_ms=10_000, ttl_ms=30_000),
        grant=AsyncMock(side_effect=LeaseIneligibleError("worker-1")),
    )
    authorize = AsyncMock(return_value=WorkerLeaseEligibility("worker-1", True, ""))
    monkeypatch.setattr(module, "_redis_client", AsyncMock(return_value=object()))
    monkeypatch.setattr(lease_module, "get_worker_lease_eligibility", authorize)
    monkeypatch.setattr(lease_module, "LeaseStore", lambda *_args, **_kwargs: store)

    response = await module._grant_direct_lease(_worker(), _lease_request())

    assert response.data["revoked"] is True


@pytest.mark.asyncio
async def test_direct_authority_failure_is_service_unavailable(monkeypatch) -> None:
    store = SimpleNamespace(policy=SimpleNamespace(renew_after_ms=10_000, ttl_ms=30_000), grant=AsyncMock())
    authorize = AsyncMock(side_effect=RuntimeError("postgres unavailable"))
    monkeypatch.setattr(module, "_redis_client", AsyncMock(return_value=object()))
    monkeypatch.setattr(lease_module, "get_worker_lease_eligibility", authorize)
    monkeypatch.setattr(lease_module, "LeaseStore", lambda *_args, **_kwargs: store)

    with pytest.raises(HTTPException) as caught:
        await module._grant_direct_lease(_worker(), _lease_request())

    assert caught.value.status_code == HTTP_SERVICE_UNAVAILABLE
    store.grant.assert_not_awaited()
