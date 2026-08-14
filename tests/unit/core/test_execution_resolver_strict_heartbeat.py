from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.scheduler.execution_resolver import ExecutionResolver
from antcode_core.application.services.workers import worker_heartbeat_service
from antcode_core.common.exceptions import WorkerUnavailableError
from antcode_core.domain.models import WorkerStatus


@pytest.mark.asyncio
async def test_worker_online_check_exposes_heartbeat_failures(monkeypatch):
    resolver = ExecutionResolver()
    worker = SimpleNamespace(id=1, public_id="worker-1", status=WorkerStatus.OFFLINE, last_heartbeat=None)
    monkeypatch.setattr(
        "antcode_core.application.services.scheduler.execution_resolver.has_unacknowledged_v2_registration",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        worker_heartbeat_service,
        "manual_test_worker",
        AsyncMock(side_effect=RuntimeError("redis unavailable")),
    )

    with pytest.raises(WorkerUnavailableError, match="redis unavailable"):
        await resolver._ensure_worker_online(worker)


@pytest.mark.asyncio
async def test_worker_online_check_rejects_unacknowledged_v2_registration(monkeypatch):
    resolver = ExecutionResolver()
    worker = SimpleNamespace(id=1, public_id="worker-1", status=WorkerStatus.ONLINE)
    pending = AsyncMock(return_value=True)
    manual_test = AsyncMock()
    monkeypatch.setattr(
        "antcode_core.application.services.scheduler.execution_resolver.has_unacknowledged_v2_registration",
        pending,
    )
    monkeypatch.setattr(worker_heartbeat_service, "manual_test_worker", manual_test)

    assert await resolver._ensure_worker_online(worker) is False
    pending.assert_awaited_once_with("worker-1")
    manual_test.assert_not_awaited()
