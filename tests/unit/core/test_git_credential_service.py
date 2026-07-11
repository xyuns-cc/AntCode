from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from antcode_core.application.services.projects.git_credential_service import (
    git_credential_service,
)
from antcode_core.domain.schemas.git_credential import GitCredentialCreateRequest
from fastapi import HTTPException
from pydantic import ValidationError


@pytest.mark.asyncio
async def test_ensure_accessible_rejects_other_user_credential():
    with patch.object(
        git_credential_service,
        "get_for_user",
        AsyncMock(return_value=None),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await git_credential_service.ensure_accessible("cred-001", 100)

    assert exc_info.value.status_code == 400
    assert "Git 凭证不存在" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_build_auth_config_rejects_host_scope_mismatch():
    credential = SimpleNamespace(
        public_id="cred-001",
        auth_type="token",
        username=None,
        secret_encrypted="encrypted-secret",
        host_scope="git.example.com",
    )

    with patch.object(
        git_credential_service,
        "get_by_public_id",
        AsyncMock(return_value=credential),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await git_credential_service.build_auth_config(
                "https://github.com/org/repo.git",
                "cred-001",
            )

    assert exc_info.value.status_code == 400
    assert "host_scope 不匹配" in str(exc_info.value.detail)


def test_create_request_requires_username_for_basic_auth():
    with pytest.raises(ValidationError) as exc_info:
        GitCredentialCreateRequest(
            name="GitHub Basic",
            auth_type="basic",
            username=None,
            secret="secret-123",
            host_scope="github.com",
        )

    assert "Basic 认证必须提供 username" in str(exc_info.value)


@pytest.mark.asyncio
async def test_update_for_user_rejects_clearing_username_on_basic_credential():
    credential = SimpleNamespace(
        auth_type="basic",
        username="bot",
        name="GitHub Basic",
        host_scope="github.com",
        secret_encrypted="encrypted",
        save=AsyncMock(),
    )
    payload = SimpleNamespace(
        name=None,
        auth_type=None,
        username="   ",
        host_scope=None,
        secret=None,
    )

    with patch.object(
        git_credential_service,
        "get_for_user",
        AsyncMock(return_value=credential),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await git_credential_service.update_for_user("cred-001", 100, payload)

    assert exc_info.value.status_code == 400
    assert "Basic 凭证必须提供 username" in str(exc_info.value.detail)
