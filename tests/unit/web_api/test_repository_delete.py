import socket
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.projects.repository_service import RepositoryDeleteStatus
from antcode_core.domain.schemas.repository import RepositoryCreateRequest
from antcode_web_api.routes.v1 import repositories
from fastapi import HTTPException, status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "status_code"),
    [
        (RepositoryDeleteStatus.NOT_FOUND, 404),
        (RepositoryDeleteStatus.IN_USE, 409),
    ],
)
async def test_delete_repository_maps_service_conflicts(monkeypatch, result, status_code: int) -> None:
    delete = AsyncMock(return_value=result)
    monkeypatch.setattr(repositories.repository_service, "delete_for_user", delete)

    with pytest.raises(HTTPException) as exc_info:
        await repositories.delete_repository("repo-1", current_user_id=7)

    assert exc_info.value.status_code == status_code
    delete.assert_awaited_once_with("repo-1", 7)


@pytest.mark.asyncio
async def test_delete_repository_returns_success(monkeypatch) -> None:
    delete = AsyncMock(return_value=RepositoryDeleteStatus.DELETED)
    monkeypatch.setattr(repositories.repository_service, "delete_for_user", delete)

    response = await repositories.delete_repository("repo-1", current_user_id=7)

    assert response.success is True
    delete.assert_awaited_once_with("repo-1", 7)


@pytest.mark.asyncio
async def test_create_repository_matches_http_and_business_created_status(monkeypatch) -> None:
    # 建仓现在会在边界校验 Git URL（含 DNS pinning），固定解析到公网地址。
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))],
    )
    now = datetime(2026, 7, 30)
    repository = SimpleNamespace(
        public_id="repo-1",
        name="source",
        url="https://example.com/source.git",
        default_ref="main",
        credential_id=None,
        enabled=True,
        last_scan_status=None,
        last_scan_error=None,
        last_scan_result=None,
        last_scanned_at=None,
        created_at=now,
        updated_at=now,
    )
    create = AsyncMock(return_value=repository)
    monkeypatch.setattr(repositories.repository_service, "create_for_user", create)
    payload = RepositoryCreateRequest(name="source", url=repository.url)

    response = await repositories.create_repository(payload, current_user_id=7)

    assert response.code == status.HTTP_201_CREATED
    assert response.data.id == "repo-1"
    create.assert_awaited_once_with(7, payload)
