import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.workers.distributed_log_service import DistributedLogService
from antcode_web_api.routes.v1 import workers as workers_route
from fastapi import HTTPException, status


@pytest.mark.asyncio
async def test_distributed_status_rejection_has_no_success_side_effects(monkeypatch):
    service = DistributedLogService(SimpleNamespace())
    monkeypatch.setattr(service, "_update_runtime_status", AsyncMock(return_value=False))
    append_log = AsyncMock()
    push_status = AsyncMock()
    monkeypatch.setattr(service, "append_log", append_log)
    monkeypatch.setattr(service, "_push_task_status", push_status)

    assert await service.update_task_status("run-1", "success") is False
    assert service._task_status == {}
    append_log.assert_not_awaited()
    push_status.assert_not_awaited()
