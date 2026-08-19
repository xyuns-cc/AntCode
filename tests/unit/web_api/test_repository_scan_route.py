"""扫描路由：ref 透传 + 扫描失败落成可读的 4xx。"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.projects.repository_service import RepositoryScanError
from antcode_core.domain.schemas.repository import RepositoryScanRequest
from antcode_web_api.routes.v1 import repositories
from fastapi import HTTPException, status

OWNER_USER_ID = 7


def _repository_row() -> SimpleNamespace:
    now = datetime(2026, 8, 19)
    return SimpleNamespace(
        public_id="repo-1",
        name="source",
        url="https://example.test/source.git",
        default_ref="main",
        credential_id=None,
        enabled=True,
        last_scan_status="success",
        last_scan_error=None,
        last_scan_result=[],
        last_scanned_at=now,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_scan_repository_reports_scan_failure_with_its_reason(monkeypatch) -> None:
    """此前 ``ValueError`` 直接逃逸，用户只看到 500「服务器内部错误」。"""
    scan = AsyncMock(side_effect=RepositoryScanError("无法解析 Git 引用版本"))
    monkeypatch.setattr(repositories.repository_service, "scan_for_user", scan)

    with pytest.raises(HTTPException) as exc_info:
        await repositories.scan_repository(
            "repo-1",
            RepositoryScanRequest(ref="no-such-branch"),
            current_user_id=OWNER_USER_ID,
        )

    assert exc_info.value.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert exc_info.value.detail == "无法解析 Git 引用版本"
    scan.assert_awaited_once_with("repo-1", OWNER_USER_ID, "no-such-branch")


@pytest.mark.asyncio
async def test_scan_repository_echoes_the_requested_ref(monkeypatch) -> None:
    repository = _repository_row()
    scan = AsyncMock(return_value=(repository, []))
    monkeypatch.setattr(repositories.repository_service, "scan_for_user", scan)

    response = await repositories.scan_repository(
        "repo-1",
        RepositoryScanRequest(ref="feature/spiders"),
        current_user_id=OWNER_USER_ID,
    )

    assert response.data.ref == "feature/spiders"
    scan.assert_awaited_once_with("repo-1", OWNER_USER_ID, "feature/spiders")


@pytest.mark.asyncio
async def test_scan_repository_falls_back_to_the_stored_default_ref(monkeypatch) -> None:
    repository = _repository_row()
    scan = AsyncMock(return_value=(repository, []))
    monkeypatch.setattr(repositories.repository_service, "scan_for_user", scan)

    response = await repositories.scan_repository(
        "repo-1",
        RepositoryScanRequest(),
        current_user_id=OWNER_USER_ID,
    )

    assert response.data.ref == "main"
    scan.assert_awaited_once_with("repo-1", OWNER_USER_ID, None)
