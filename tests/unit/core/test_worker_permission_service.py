from contextlib import AbstractAsyncContextManager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.workers import worker_permission_service as permission_module
from antcode_core.application.services.workers import worker_service as worker_service_instance
from antcode_core.domain import models


class _Transaction(AbstractAsyncContextManager):
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


@pytest.mark.asyncio
async def test_service_rejects_unknown_permission_before_database(monkeypatch) -> None:
    transaction = AsyncMock()
    monkeypatch.setattr(permission_module, "in_transaction", transaction)

    with pytest.raises(ValueError, match="view 或 use"):
        await permission_module.worker_permission_service.assign(
            worker_id=1,
            user_id=2,
            permission="admin",
            assigned_by=3,
            note=None,
        )

    transaction.assert_not_called()


@pytest.mark.asyncio
async def test_batch_service_fails_when_any_worker_disappears(monkeypatch) -> None:
    monkeypatch.setattr(permission_module, "in_transaction", lambda _name: _Transaction())
    monkeypatch.setattr(permission_module, "_lock_user", AsyncMock())
    lock_workers = AsyncMock(side_effect=permission_module.WorkerPermissionTargetNotFound("Worker 不存在: 12"))
    monkeypatch.setattr(permission_module, "_lock_workers", lock_workers)
    bulk_create = AsyncMock()
    monkeypatch.setattr(permission_module.UserWorkerPermission, "bulk_create", bulk_create)

    with pytest.raises(permission_module.WorkerPermissionTargetNotFound, match="12"):
        await permission_module.worker_permission_service.batch_assign(
            user_id=2,
            worker_ids=[11, 12],
            permission="use",
            assigned_by=3,
        )

    bulk_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_accessible_workers_ignore_invalid_stored_permissions(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Query:
        async def all(self):
            return []

    def filter_permissions(**filters):
        captured.update(filters)
        return Query()

    monkeypatch.setattr(models, "UserWorkerPermission", SimpleNamespace(filter=filter_permissions))

    workers = await worker_service_instance.get_user_workers(user_id=7)

    assert workers == []
    assert captured["permission__in"] == ("view", "use")
