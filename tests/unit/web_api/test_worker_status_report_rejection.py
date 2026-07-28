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


@pytest.mark.asyncio
async def test_http_status_report_exposes_rejected_business_update(monkeypatch):
    update = AsyncMock(return_value=False)
    log_service_module = importlib.import_module("antcode_core.application.services.workers.distributed_log_service")
    ownership_module = importlib.import_module("antcode_core.application.services.workers.run_ownership_service")
    monkeypatch.setattr(ownership_module, "require_worker_owns_run", AsyncMock())
    monkeypatch.setattr(
        log_service_module.distributed_log_service,
        "update_task_status",
        update,
    )
    request = workers_route.WorkerTaskStatusReportRequest(run_id="run-1", status="success")

    with pytest.raises(HTTPException) as exc_info:
        await workers_route.report_task_status(request, auth_context={"worker": object()})

    assert exc_info.value.status_code == status.HTTP_409_CONFLICT
