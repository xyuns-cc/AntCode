"""Git 凭证路由测试。"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from antcode_core.domain.schemas.git_credential import GitCredentialCreateRequest
from antcode_web_api.routes.v1 import git_credentials as route
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_list_git_credentials_returns_public_fields_only():
    credential = SimpleNamespace(
        public_id="cred-001",
        name="GitHub Token",
        auth_type="token",
        username=None,
        secret_encrypted="encrypted",
        host_scope="github.com",
        created_at=datetime(2026, 3, 9),
        updated_at=datetime(2026, 3, 9),
    )

    with patch.object(
        route.git_credential_service,
        "list_for_user",
        AsyncMock(return_value=[credential]),
    ):
        response = await route.list_git_credentials(current_user_id=100)

    assert response.success is True
    assert response.data[0].id == "cred-001"
    assert response.data[0].name == "GitHub Token"
    assert response.data[0].has_secret is True
    assert not hasattr(response.data[0], "secret")
    assert not hasattr(response.data[0], "secret_encrypted")


@pytest.mark.asyncio
async def test_create_git_credential_returns_created_response():
    payload = GitCredentialCreateRequest(
        name="GitLab Basic",
        auth_type="basic",
        username="bot",
        secret="secret-123",
        host_scope="gitlab.com",
    )
    credential = SimpleNamespace(
        public_id="cred-002",
        name="GitLab Basic",
        auth_type="basic",
        username="bot",
        secret_encrypted="encrypted",
        host_scope="gitlab.com",
        created_at=datetime(2026, 3, 9),
        updated_at=datetime(2026, 3, 9),
    )

    with patch.object(
        route.git_credential_service,
        "create_for_user",
        AsyncMock(return_value=credential),
    ) as create_for_user:
        response = await route.create_git_credential(payload, current_user_id=101)

    create_for_user.assert_awaited_once_with(101, payload)
    assert response.code == 201
    assert response.data.id == "cred-002"
    assert response.data.host_scope == "gitlab.com"


@pytest.mark.asyncio
async def test_get_git_credential_raises_not_found():
    with patch.object(
        route.git_credential_service,
        "get_for_user",
        AsyncMock(return_value=None),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await route.get_git_credential("missing", current_user_id=102)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Git 凭证不存在"
