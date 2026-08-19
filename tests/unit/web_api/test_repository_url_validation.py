"""建仓/改仓必须在写入边界校验 Git URL。

走查实测：``POST /api/v1/repositories`` 曾以 201 接受并落库
``file:///etc/passwd``，直到点「扫描导入」才在 ``resolve_git_url`` 抛
``ValueError`` 逃逸成 500「服务器内部错误」，用户拿不到任何可操作信息。
"""

import socket
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.projects import git_url_security
from antcode_core.domain.schemas.repository import RepositoryCreateRequest, RepositoryUpdateRequest
from antcode_web_api.routes.v1 import repositories
from fastapi import HTTPException, status

PUBLIC_ADDRESS = "93.184.216.34"
UNPROCESSABLE = status.HTTP_422_UNPROCESSABLE_CONTENT


@pytest.fixture(autouse=True)
def _disallow_private_nodes(monkeypatch) -> None:
    monkeypatch.setattr(git_url_security.settings, "ALLOW_PRIVATE_NODES", False)


@pytest.fixture
def _public_dns(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_ADDRESS, 0))],
    )


def _repository_row(url: str) -> SimpleNamespace:
    now = datetime(2026, 8, 19)
    return SimpleNamespace(
        public_id="repo-1",
        name="source",
        url=url,
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "message_fragment"),
    [
        ("file:///etc/passwd", "仅支持"),
        ("ext::sh -c id", "remote helper"),
        ("http://169.254.169.254/latest/meta-data/", "元数据"),
        ("https://10.0.0.1/repo.git", "私网"),
    ],
)
async def test_create_repository_rejects_unusable_git_url(monkeypatch, url: str, message_fragment: str) -> None:
    create = AsyncMock()
    monkeypatch.setattr(repositories.repository_service, "create_for_user", create)

    with pytest.raises(HTTPException) as exc_info:
        await repositories.create_repository(RepositoryCreateRequest(name="source", url=url), current_user_id=7)

    assert exc_info.value.status_code == UNPROCESSABLE
    assert message_fragment in str(exc_info.value.detail)
    # 必须在落库之前就拒掉，而不是先写进 DB 再等 scan 阶段 500。
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_repository_rejects_unusable_git_url(monkeypatch) -> None:
    update = AsyncMock()
    monkeypatch.setattr(repositories.repository_service, "update_for_user", update)

    with pytest.raises(HTTPException) as exc_info:
        await repositories.update_repository(
            "repo-1",
            RepositoryUpdateRequest(url="file:///etc/passwd"),
            current_user_id=7,
        )

    assert exc_info.value.status_code == UNPROCESSABLE
    update.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_repository_accepts_public_git_url(monkeypatch, _public_dns) -> None:
    url = "https://example.com/source.git"
    create = AsyncMock(return_value=_repository_row(url))
    monkeypatch.setattr(repositories.repository_service, "create_for_user", create)

    response = await repositories.create_repository(RepositoryCreateRequest(name="source", url=url), current_user_id=7)

    assert response.data.url == url
    create.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_without_url_skips_validation(monkeypatch) -> None:
    """只改名字/开关时不带 url，不应触发 Git URL 校验（含 DNS）。"""

    def unexpected_dns(*_args, **_kwargs):
        pytest.fail("未提供 url 的更新不应触发 DNS 解析")

    monkeypatch.setattr(socket, "getaddrinfo", unexpected_dns)
    update = AsyncMock(return_value=_repository_row("https://example.com/source.git"))
    monkeypatch.setattr(repositories.repository_service, "update_for_user", update)

    response = await repositories.update_repository(
        "repo-1", RepositoryUpdateRequest(name="renamed"), current_user_id=7
    )

    assert response.success is True
    update.assert_awaited_once()
