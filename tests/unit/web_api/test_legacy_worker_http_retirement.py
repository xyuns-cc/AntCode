from unittest.mock import AsyncMock

import pytest
from antcode_core.domain.schemas.worker import WorkerHeartbeatRequest
from antcode_web_api.routes.v1 import workers_register
from fastapi import HTTPException, status


@pytest.mark.asyncio
async def test_legacy_worker_heartbeat_cannot_mutate_worker_state(monkeypatch) -> None:
    heartbeat = AsyncMock()
    monkeypatch.setattr(workers_register.worker_service, "heartbeat", heartbeat)
    request = WorkerHeartbeatRequest(
        worker_id="worker-1",
        api_key="retired",
        status="online",
    )

    with pytest.raises(HTTPException) as exc_info:
        await workers_register.worker_heartbeat(request, {"worker_id": "worker-1"})

    assert exc_info.value.status_code == status.HTTP_410_GONE
    heartbeat.assert_not_awaited()
