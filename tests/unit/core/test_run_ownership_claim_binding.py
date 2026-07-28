from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.workers import run_ownership_service as module


def _install_worker(monkeypatch) -> None:
    monkeypatch.setattr(
        module.Worker,
        "get_or_none",
        AsyncMock(return_value=SimpleNamespace(id=7)),
    )


@pytest.mark.asyncio
async def test_first_claim_atomically_binds_null_taskrun_lease(monkeypatch):
    _install_worker(monkeypatch)
    execution = SimpleNamespace(id=11, worker_id=7, lease_id=None)
    lookup = SimpleNamespace(first=AsyncMock(return_value=execution))
    update = SimpleNamespace(update=AsyncMock(return_value=1))
    filters: list[dict] = []

    def task_run_filter(**kwargs):
        filters.append(kwargs)
        return lookup if "run_id" in kwargs else update

    monkeypatch.setattr(module.TaskRun, "filter", task_run_filter)

    await module.require_or_bind_worker_run_lease(
        "worker-1",
        "run-1",
        lease_id="lease-new",
    )

    update.update.assert_awaited_once_with(lease_id="lease-new")
    assert filters[-1] == {"id": 11, "worker_id": 7, "lease_id__isnull": True}


@pytest.mark.asyncio
async def test_claim_rejects_taskrun_bound_to_old_lease(monkeypatch):
    _install_worker(monkeypatch)
    execution = SimpleNamespace(id=11, worker_id=7, lease_id="lease-old")
    lookup = SimpleNamespace(first=AsyncMock(return_value=execution))
    monkeypatch.setattr(module.TaskRun, "filter", lambda **_kwargs: lookup)

    with pytest.raises(PermissionError, match="代际不匹配"):
        await module.require_or_bind_worker_run_lease(
            "worker-1",
            "run-1",
            lease_id="lease-new",
        )


@pytest.mark.asyncio
async def test_claim_retry_accepts_same_taskrun_lease_without_update(monkeypatch):
    _install_worker(monkeypatch)
    execution = SimpleNamespace(id=11, worker_id=7, lease_id="lease-current")
    lookup = SimpleNamespace(first=AsyncMock(return_value=execution))
    monkeypatch.setattr(module.TaskRun, "filter", lambda **_kwargs: lookup)

    await module.require_or_bind_worker_run_lease(
        "worker-1",
        "run-1",
        lease_id="lease-current",
    )


@pytest.mark.asyncio
async def test_concurrent_same_lease_binding_is_idempotent(monkeypatch):
    _install_worker(monkeypatch)
    execution = SimpleNamespace(id=11, worker_id=7, lease_id=None)
    lookup = SimpleNamespace(first=AsyncMock(return_value=execution))
    update = SimpleNamespace(update=AsyncMock(return_value=0))
    matching = SimpleNamespace(exists=AsyncMock(return_value=True))

    def task_run_filter(**kwargs):
        if "run_id" in kwargs:
            return lookup
        if "lease_id__isnull" in kwargs:
            return update
        return matching

    monkeypatch.setattr(module.TaskRun, "filter", task_run_filter)

    await module.require_or_bind_worker_run_lease(
        "worker-1",
        "run-1",
        lease_id="lease-current",
    )

    matching.exists.assert_awaited_once()
