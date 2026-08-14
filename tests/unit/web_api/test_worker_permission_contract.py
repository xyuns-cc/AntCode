from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.domain.schemas.worker_permission import (
    WorkerPermissionAssignRequest,
    WorkerPermissionBatchAssignRequest,
)
from antcode_web_api.routes.v1 import workers, workers_permission
from fastapi import HTTPException
from pydantic import ValidationError

HTTP_NOT_FOUND = 404
EXPECTED_ASSIGNED_WORKERS = 2


@pytest.mark.parametrize(
    "payload",
    [
        {"user_id": "user-1", "permission": "admin"},
        {"user_id": True, "permission": "use"},
        {"user_id": "user-1", "permission": "use", "unexpected": True},
    ],
)
def test_assign_request_rejects_invalid_payload(payload: dict) -> None:
    with pytest.raises(ValidationError):
        WorkerPermissionAssignRequest.model_validate(payload)


def test_batch_request_requires_non_empty_strict_identifiers() -> None:
    with pytest.raises(ValidationError):
        WorkerPermissionBatchAssignRequest(user_id="user-1", worker_ids=[])
    with pytest.raises(ValidationError):
        WorkerPermissionBatchAssignRequest(user_id="user-1", worker_ids=[True])


def test_permission_routes_publish_structured_request_models() -> None:
    assign_route = next(route for route in workers.router.routes if route.path == "/{worker_id}/assign")
    batch_route = next(route for route in workers.router.routes if route.path == "/batch-assign")

    assert assign_route.dependant.body_params[0].field_info.annotation is WorkerPermissionAssignRequest
    assert batch_route.dependant.body_params[0].field_info.annotation is WorkerPermissionBatchAssignRequest


@pytest.mark.asyncio
async def test_batch_resolution_rejects_any_missing_worker(monkeypatch) -> None:
    class Query:
        def only(self, *_fields):
            return self

        async def all(self):
            return [SimpleNamespace(id=1, public_id="worker-1")]

    monkeypatch.setattr(workers_permission.Worker, "filter", lambda *_args, **_kwargs: Query())

    with pytest.raises(HTTPException) as exc_info:
        await workers_permission._resolve_workers(["worker-1", "worker-missing"])

    assert exc_info.value.status_code == HTTP_NOT_FOUND
    assert "worker-missing" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_batch_assignment_uses_resolved_user_and_all_workers(monkeypatch) -> None:
    assign = AsyncMock(return_value={"success": 2, "failed": 0, "skipped": 0})
    monkeypatch.setattr(workers_permission, "_require_admin", AsyncMock())
    monkeypatch.setattr(workers_permission, "_resolve_user", AsyncMock(return_value=SimpleNamespace(id=8)))
    monkeypatch.setattr(workers_permission, "_resolve_workers", AsyncMock(return_value=[11, 12]))
    monkeypatch.setattr(workers_permission.worker_permission_service, "batch_assign", assign)
    request = WorkerPermissionBatchAssignRequest(
        user_id="user-public-id",
        worker_ids=["worker-a", "worker-b"],
        permission="view",
    )

    response = await workers_permission.batch_assign_workers(request, SimpleNamespace(user_id=1))

    assert response.data["success"] == EXPECTED_ASSIGNED_WORKERS
    assign.assert_awaited_once_with(
        user_id=8,
        worker_ids=[11, 12],
        permission="view",
        assigned_by=1,
    )
