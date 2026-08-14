from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.workers.worker_service import WorkerService
from antcode_core.domain.schemas.worker import WorkerUpdateRequest
from pydantic import ValidationError


def _worker(status: str = "online"):
    worker = SimpleNamespace(
        id=1,
        public_id="worker-1",
        name="worker-1",
        host="127.0.0.1",
        port=8001,
        status=status,
        api_key_hash="api-hash",
        api_key_previous_hash=None,
        api_key_previous_expires_at=None,
        secret_key_hash="secret-hash",
        secret_key_encrypted="encrypted-secret",
        save=AsyncMock(),
    )

    async def update_from_dict(values):
        for name, value in values.items():
            setattr(worker, name, value)

    worker.update_from_dict = update_from_dict
    return worker


@pytest.mark.asyncio
async def test_update_to_maintenance_disables_lease_before_database_save() -> None:
    events: list[str] = []
    disabler = AsyncMock(side_effect=lambda *_args: events.append("disable"))
    service = WorkerService(lease_disabler=disabler)
    worker = _worker()
    service.get_worker_by_id = AsyncMock(return_value=worker)
    worker.save = AsyncMock(side_effect=lambda **_kwargs: events.append("save"))

    updated = await service.update_worker("worker-1", WorkerUpdateRequest(status="maintenance"))

    assert updated.status == "maintenance"
    assert events == ["disable", "save"]
    assert updated.api_key_hash == "api-hash"
    assert updated.secret_key_encrypted == "encrypted-secret"
    disabler.assert_awaited_once_with("worker-1", "status:maintenance")


@pytest.mark.asyncio
async def test_disconnect_preserves_reconnectable_identity() -> None:
    disabler = AsyncMock(return_value=True)
    service = WorkerService(lease_disabler=disabler)
    worker = _worker()
    service.get_worker_by_id = AsyncMock(return_value=worker)
    service._connection_service.disconnect_worker = AsyncMock(return_value=True)

    assert await service.disconnect_worker("worker-1") is True

    assert worker.api_key_hash == "api-hash"
    assert worker.secret_key_encrypted == "encrypted-secret"
    disabler.assert_awaited_once_with("worker-1", "disconnect")


@pytest.mark.asyncio
async def test_direct_reregistration_clears_offline_fence_before_marking_online() -> None:
    events: list[str] = []
    enabler = AsyncMock(side_effect=lambda *_args: events.append("enable"))
    service = WorkerService(lease_enabler=enabler)
    worker = _worker(status="offline")
    request = SimpleNamespace(worker_id="worker-1")
    service.get_worker_by_public_id = AsyncMock(return_value=worker)
    service._connection_service.register_direct_worker = AsyncMock(
        side_effect=lambda *_args: events.append("register") or (worker, False)
    )

    result = await service.register_direct_worker(request)

    assert result == (worker, False)
    assert events == ["enable", "register"]
    enabler.assert_awaited_once_with("worker-1", ("status:offline", "offline", "disconnect"))


@pytest.mark.asyncio
async def test_direct_reregistration_rejects_online_worker_without_clearing_fence() -> None:
    enabler = AsyncMock()
    service = WorkerService(lease_enabler=enabler)
    worker = _worker(status="online")
    service.get_worker_by_public_id = AsyncMock(return_value=worker)
    service._connection_service.register_direct_worker = AsyncMock()

    with pytest.raises(RuntimeError, match="状态不允许重新注册"):
        await service.register_direct_worker(SimpleNamespace(worker_id="worker-1"))

    enabler.assert_not_awaited()
    service._connection_service.register_direct_worker.assert_not_awaited()


@pytest.mark.asyncio
async def test_disconnect_exposes_revoke_failure_and_does_not_mark_offline() -> None:
    disabler = AsyncMock(side_effect=RuntimeError("redis unavailable"))
    service = WorkerService(lease_disabler=disabler)
    worker = _worker()
    service.get_worker_by_id = AsyncMock(return_value=worker)
    service._connection_service.disconnect_worker = AsyncMock()

    with pytest.raises(RuntimeError, match="redis unavailable"):
        await service.disconnect_worker("worker-1")

    service._connection_service.disconnect_worker.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_rejects_unknown_status_without_side_effects() -> None:
    disabler = AsyncMock()
    service = WorkerService(lease_disabler=disabler)
    worker = _worker()
    service.get_worker_by_id = AsyncMock(return_value=worker)

    with pytest.raises(ValidationError):
        request = WorkerUpdateRequest(status="retired")
        await service.update_worker("worker-1", request)

    disabler.assert_not_awaited()
    worker.save.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_offline_to_online_clears_only_reconnectable_fences_before_save() -> None:
    events: list[str] = []
    enabler = AsyncMock(side_effect=lambda *_args: events.append("enable"))
    service = WorkerService(lease_enabler=enabler)
    worker = _worker(status="offline")
    service.get_worker_by_id = AsyncMock(return_value=worker)
    worker.save = AsyncMock(side_effect=lambda **_kwargs: events.append("save"))

    updated = await service.update_worker("worker-1", WorkerUpdateRequest(status="online"))

    assert updated.status == "online"
    assert events == ["enable", "save"]
    enabler.assert_awaited_once_with("worker-1", ("status:offline", "offline", "disconnect"))


@pytest.mark.asyncio
async def test_update_online_to_online_does_not_clear_unknown_fence() -> None:
    enabler = AsyncMock()
    service = WorkerService(lease_enabler=enabler)
    worker = _worker(status="online")
    service.get_worker_by_id = AsyncMock(return_value=worker)

    with pytest.raises(RuntimeError, match="不能从 online 状态"):
        await service.update_worker("worker-1", WorkerUpdateRequest(status="online"))

    enabler.assert_not_awaited()
    worker.save.assert_not_awaited()
