from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.projects.repository_service import RepositoryDeleteStatus
from antcode_web_api.routes.v1 import repositories
from fastapi import HTTPException


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
