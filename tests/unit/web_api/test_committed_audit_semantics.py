from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_core.application.services.users.user_service import user_service
from antcode_web_api import committed_audit
from antcode_web_api.routes.v1 import committed_resource_audit, workers_crud
from fastapi import Request


async def _failing_audit() -> None:
    raise RuntimeError("audit database unavailable")


@pytest.mark.asyncio
async def test_committed_audit_executes_successful_factory() -> None:
    audit_write = AsyncMock()

    recorded = await committed_audit.record_committed_audit("project_create", audit_write)

    assert recorded is True
    audit_write.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_committed_audit_failure_is_explicit_without_raising(monkeypatch) -> None:
    logger = MagicMock()
    logger.opt.return_value = logger
    monkeypatch.setattr(committed_audit, "logger", logger)

    recorded = await committed_audit.record_committed_audit("project_create", _failing_audit)

    assert recorded is False
    logger.critical.assert_called_once()


@pytest.mark.asyncio
async def test_worker_create_returns_committed_resource_when_audit_fails(monkeypatch) -> None:
    worker = SimpleNamespace(public_id="worker-1", name="worker", id=1)
    monkeypatch.setattr(workers_crud.worker_service, "create_worker", AsyncMock(return_value=worker))
    monkeypatch.setattr(
        committed_resource_audit.audit_service,
        "log",
        AsyncMock(side_effect=RuntimeError("audit down")),
    )
    monkeypatch.setattr(user_service, "get_user_by_id", AsyncMock(return_value=SimpleNamespace(username="root")))
    current_user = SimpleNamespace(user_id=7)
    http_request = Request({"type": "http", "client": ("127.0.0.1", 1234)})

    response = await workers_crud.create_worker(
        SimpleNamespace(),
        http_request,
        current_user,
        worker_to_response=lambda value: {"id": value.public_id},
    )

    assert response.data == {"id": "worker-1"}


@pytest.mark.asyncio
async def test_worker_create_returns_committed_resource_when_audit_identity_lookup_fails(monkeypatch) -> None:
    worker = SimpleNamespace(public_id="worker-1", name="worker", id=1)
    monkeypatch.setattr(workers_crud.worker_service, "create_worker", AsyncMock(return_value=worker))
    monkeypatch.setattr(
        user_service,
        "get_user_by_id",
        AsyncMock(side_effect=RuntimeError("user database unavailable")),
    )
    current_user = SimpleNamespace(user_id=7)
    http_request = Request({"type": "http", "client": ("127.0.0.1", 1234)})

    response = await workers_crud.create_worker(
        SimpleNamespace(),
        http_request,
        current_user,
        worker_to_response=lambda value: {"id": value.public_id},
    )

    assert response.data == {"id": "worker-1"}
