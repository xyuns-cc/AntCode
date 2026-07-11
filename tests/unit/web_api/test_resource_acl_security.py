from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_web_api.routes.v1 import crawl as crawl_route
from antcode_web_api.routes.v1 import monitoring as monitoring_route
from antcode_web_api.routes.v1 import workers as workers_route
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_worker_detail_hides_unassigned_worker(monkeypatch):
    worker = SimpleNamespace(id=7, public_id="worker-7")
    user = SimpleNamespace(id=11, is_admin=False, is_active=True)
    monkeypatch.setattr(workers_route.worker_service, "get_worker_by_id", AsyncMock(return_value=worker))
    monkeypatch.setattr(workers_route, "_request_user", AsyncMock(return_value=user))
    permission = AsyncMock(return_value=False)
    monkeypatch.setattr(workers_route.worker_service, "check_user_worker_permission", permission)

    with pytest.raises(HTTPException) as exc_info:
        await workers_route._require_worker_access(
            "worker-7",
            SimpleNamespace(user_id=11),
        )

    assert exc_info.value.status_code == 404
    permission.assert_awaited_once_with(
        user_id=11,
        worker_id=7,
        is_admin=False,
        required_permission="view",
    )


@pytest.mark.asyncio
async def test_monitoring_requires_worker_acl(monkeypatch):
    worker = SimpleNamespace(id=7, public_id="worker-7")
    user = SimpleNamespace(id=11, is_admin=False, is_active=True)
    monkeypatch.setattr(monitoring_route, "_ensure_authenticated_user", AsyncMock(return_value=user))
    monkeypatch.setattr(monitoring_route.Worker, "get_or_none", AsyncMock(return_value=worker))
    permission = AsyncMock(return_value=False)
    monkeypatch.setattr(monitoring_route.worker_service, "check_user_worker_permission", permission)

    with pytest.raises(HTTPException) as exc_info:
        await monitoring_route._ensure_worker_access(
            "worker-7",
            SimpleNamespace(user_id=11),
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_crawl_project_metrics_hide_foreign_project(monkeypatch):
    project = SimpleNamespace(public_id="project-7", user_id=99)
    monkeypatch.setattr(crawl_route.Project, "get_or_none", AsyncMock(return_value=project))

    with pytest.raises(HTTPException) as exc_info:
        await crawl_route._verify_project_access(
            "project-7",
            SimpleNamespace(user_id=11, is_admin=False),
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_crawl_batch_rejects_foreign_owner(monkeypatch):
    batch = SimpleNamespace(public_id="batch-7", user_id=99)
    monkeypatch.setattr(crawl_route.crawl_batch_service, "get_batch", AsyncMock(return_value=batch))

    with pytest.raises(HTTPException) as exc_info:
        await crawl_route._verify_batch_owner(
            "batch-7",
            SimpleNamespace(user_id=11, is_admin=False),
        )

    assert exc_info.value.status_code == 403
