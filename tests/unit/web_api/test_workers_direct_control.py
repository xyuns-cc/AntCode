from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.lease_service import Lease
from antcode_core.application.services.workers.run_ownership_fence import OwnershipOutcome
from antcode_web_api.routes.v1 import workers_direct_control as module
from fastapi import HTTPException
from pydantic import ValidationError

HTTP_FORBIDDEN = 403
HTTP_PRECONDITION_FAILED = 412
HTTP_SERVICE_UNAVAILABLE = 503


def _worker(*, worker_id: str = "worker-1", mode: str = "direct"):
    return SimpleNamespace(
        public_id=worker_id,
        transport_mode=mode,
        capabilities={"task_types": ["code"]},
        id=1,
    )


class _LeaseStore:
    policy = SimpleNamespace(renew_after_ms=10_000)

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    async def is_current(self, _worker_id: str, _lease_id: str) -> bool:
        return True

    async def get(self, _worker_id: str, include_expired: bool = True) -> Lease:
        assert include_expired is False
        return Lease("worker-1", "lease-1", 100_000, 100, sequence=101)


def _claim() -> module.DirectOwnershipClaimRequest:
    return module.DirectOwnershipClaimRequest(
        operation="claim",
        lease_id="lease-1",
        run_id="run-1",
        ttl_ms=60_000,
    )


def _release() -> module.DirectOwnershipReleaseRequest:
    return module.DirectOwnershipReleaseRequest(
        operation="release",
        lease_id="lease-1",
        run_id="run-1",
    )


def test_worker_identity_and_mode_are_enforced() -> None:
    with pytest.raises(HTTPException) as mismatch:
        module._authenticated_worker("worker-1", {"worker": _worker(worker_id="worker-2")})
    assert mismatch.value.status_code == HTTP_FORBIDDEN
    with pytest.raises(HTTPException) as wrong_mode:
        module._authenticated_worker("worker-1", {"worker": _worker(mode="gateway")})
    assert wrong_mode.value.status_code == HTTP_FORBIDDEN


def test_operation_discriminator_cannot_cross_endpoints() -> None:
    with pytest.raises(ValidationError):
        module.DirectOwnershipRenewRequest(
            operation="claim",
            lease_id="lease-1",
            run_id="run-1",
            ttl_ms=60_000,
        )


@pytest.mark.asyncio
async def test_claim_fences_then_binds_before_success(monkeypatch) -> None:
    events: list[str] = []
    monkeypatch.setattr(module, "get_redis_client", AsyncMock(return_value=object()))
    monkeypatch.setattr(module, "LeaseStore", _LeaseStore)
    monkeypatch.setattr(
        module,
        "require_worker_owns_runs_for_lease",
        AsyncMock(side_effect=lambda *_args, **_kwargs: events.append("owns")),
    )
    monkeypatch.setattr(
        module,
        "claim_run_ownership",
        AsyncMock(side_effect=lambda *_args, **_kwargs: events.append("fence") or OwnershipOutcome.ACQUIRED),
    )
    monkeypatch.setattr(
        module,
        "bind_worker_run_lease_generation",
        AsyncMock(side_effect=lambda *_args, **_kwargs: events.append("bind")),
    )
    monkeypatch.setattr(module, "read_log_ingest_cutoff", AsyncMock(return_value="10-0"))
    monkeypatch.setattr(
        module,
        "renew_run_ownership",
        AsyncMock(side_effect=lambda *_args, **_kwargs: events.append("renew") or OwnershipOutcome.ACQUIRED),
    )

    response = await module._claim_direct_ownership(_worker(), _claim())

    assert response.data == {"acquired": True}
    assert events == ["owns", "fence", "bind", "renew"]
    bind = module.bind_worker_run_lease_generation
    assert bind.await_args.kwargs["log_cutoff_id"] == "10-0"


@pytest.mark.asyncio
async def test_bind_failure_releases_exact_claim(monkeypatch) -> None:
    redis = object()
    release = AsyncMock(return_value=True)
    monkeypatch.setattr(module, "get_redis_client", AsyncMock(return_value=redis))
    monkeypatch.setattr(module, "LeaseStore", _LeaseStore)
    monkeypatch.setattr(module, "require_worker_owns_runs_for_lease", AsyncMock())
    monkeypatch.setattr(module, "claim_run_ownership", AsyncMock(return_value=OwnershipOutcome.ACQUIRED))
    monkeypatch.setattr(module, "read_log_ingest_cutoff", AsyncMock(return_value="10-0"))
    monkeypatch.setattr(module, "bind_worker_run_lease_generation", AsyncMock(side_effect=RuntimeError("db down")))
    monkeypatch.setattr(module, "release_run_ownership", release)

    with pytest.raises(HTTPException) as exc_info:
        await module._claim_direct_ownership(_worker(), _claim())

    assert exc_info.value.status_code == HTTP_SERVICE_UNAVAILABLE
    release.assert_awaited_once_with(
        redis,
        worker_id="worker-1",
        lease_id="lease-1",
        run_id="run-1",
        namespace=module.redis_namespace(),
    )


@pytest.mark.asyncio
async def test_compensation_failure_remains_failed(monkeypatch) -> None:
    monkeypatch.setattr(module, "get_redis_client", AsyncMock(return_value=object()))
    monkeypatch.setattr(module, "LeaseStore", _LeaseStore)
    monkeypatch.setattr(module, "require_worker_owns_runs_for_lease", AsyncMock())
    monkeypatch.setattr(module, "claim_run_ownership", AsyncMock(return_value=OwnershipOutcome.ACQUIRED))
    monkeypatch.setattr(module, "read_log_ingest_cutoff", AsyncMock(return_value="10-0"))
    monkeypatch.setattr(module, "bind_worker_run_lease_generation", AsyncMock(side_effect=RuntimeError("db down")))
    monkeypatch.setattr(module, "release_run_ownership", AsyncMock(side_effect=RuntimeError("redis down")))

    with pytest.raises(HTTPException) as exc_info:
        await module._claim_direct_ownership(_worker(), _claim())
    assert exc_info.value.status_code == HTTP_SERVICE_UNAVAILABLE


@pytest.mark.asyncio
async def test_final_fence_stale_releases_claim_and_returns_precondition_failed(monkeypatch) -> None:
    redis = object()
    release = AsyncMock(return_value=True)
    monkeypatch.setattr(module, "get_redis_client", AsyncMock(return_value=redis))
    monkeypatch.setattr(module, "LeaseStore", _LeaseStore)
    monkeypatch.setattr(module, "require_worker_owns_runs_for_lease", AsyncMock())
    monkeypatch.setattr(module, "claim_run_ownership", AsyncMock(return_value=OwnershipOutcome.ACQUIRED))
    monkeypatch.setattr(module, "read_log_ingest_cutoff", AsyncMock(return_value="10-0"))
    monkeypatch.setattr(module, "bind_worker_run_lease_generation", AsyncMock())
    monkeypatch.setattr(module, "renew_run_ownership", AsyncMock(return_value=OwnershipOutcome.LEASE_STALE))
    monkeypatch.setattr(module, "release_run_ownership", release)

    with pytest.raises(HTTPException) as exc_info:
        await module._claim_direct_ownership(_worker(), _claim())

    assert exc_info.value.status_code == HTTP_PRECONDITION_FAILED
    release.assert_awaited_once_with(
        redis,
        worker_id="worker-1",
        lease_id="lease-1",
        run_id="run-1",
        namespace=module.redis_namespace(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("released", [True, False])
async def test_release_uses_authenticated_token_without_live_run_or_lease(
    monkeypatch,
    released: bool,
) -> None:
    redis = object()
    release = AsyncMock(return_value=released)
    run_lookup = AsyncMock(side_effect=AssertionError("release must not query TaskRun"))
    monkeypatch.setattr(module, "_redis_client", AsyncMock(return_value=redis))
    monkeypatch.setattr(module, "require_worker_owns_runs_for_lease", run_lookup)
    monkeypatch.setattr(
        module,
        "LeaseStore",
        lambda *_args, **_kwargs: pytest.fail("release must not query current Lease"),
    )
    monkeypatch.setattr(module, "release_run_ownership", release)

    response = await module._release_direct_ownership(_worker(), _release())

    assert response.data == {"released": released}
    run_lookup.assert_not_awaited()
    release.assert_awaited_once_with(
        redis,
        worker_id="worker-1",
        lease_id="lease-1",
        run_id="run-1",
        namespace=module.redis_namespace(),
    )


@pytest.mark.asyncio
async def test_release_exposes_redis_cas_failure(monkeypatch) -> None:
    monkeypatch.setattr(module, "_redis_client", AsyncMock(return_value=object()))
    monkeypatch.setattr(
        module,
        "require_worker_owns_runs_for_lease",
        AsyncMock(side_effect=AssertionError("release must not query TaskRun")),
    )
    monkeypatch.setattr(
        module,
        "release_run_ownership",
        AsyncMock(side_effect=RuntimeError("redis down")),
    )

    with pytest.raises(HTTPException) as exc_info:
        await module._release_direct_ownership(_worker(), _release())

    assert exc_info.value.status_code == HTTP_SERVICE_UNAVAILABLE
    assert exc_info.value.detail == "run ownership persistence unavailable"
