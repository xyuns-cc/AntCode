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
    worker = SimpleNamespace(id=1, status=WorkerStatus.OFFLINE, last_heartbeat=None)
    monkeypatch.setattr(
        worker_heartbeat_service,
        "manual_test_worker",
        AsyncMock(side_effect=RuntimeError("redis unavailable")),
    )

    with pytest.raises(WorkerUnavailableError, match="redis unavailable"):
        await resolver._ensure_worker_online(worker)
