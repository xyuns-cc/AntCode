"""Git 凭证服务测试。"""

from __future__ import annotations

import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from antcode_core.application.services.projects.git_credential_service import GitCredentialService
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_ensure_accessible_rejects_other_users_credential():
    service = GitCredentialService()

    with patch.object(service, "get_for_user", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc_info:
            await service.ensure_accessible("cred-001", 100)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Git 凭证不存在"


@pytest.mark.asyncio
async def test_build_auth_config_for_basic_credential():
    service = GitCredentialService()
    credential = SimpleNamespace(
        public_id="cred-001",
        auth_type="basic",
        username="octocat",
        secret_encrypted="encrypted",
        host_scope="github.com",
    )
    expected = base64.b64encode(b"octocat:s3cr3t").decode("utf-8")

    with (
        patch.object(service, "get_by_public_id", AsyncMock(return_value=credential)),
        patch(
            "antcode_core.application.services.projects.git_credential_service.secret_box.decrypt",
            return_value="s3cr3t",
        ),
    ):
        config = await service.build_auth_config(
            "https://github.com/openai/antcode.git",
            "cred-001",
        )

    assert config.credential_public_id == "cred-001"
    assert config.header_value == f"Authorization: Basic {expected}"


@pytest.mark.asyncio
async def test_build_auth_config_rejects_host_scope_mismatch():
    service = GitCredentialService()
    credential = SimpleNamespace(
        public_id="cred-001",
        auth_type="token",
        username=None,
        secret_encrypted="encrypted",
        host_scope="gitlab.com",
    )

    with (
        patch.object(service, "get_by_public_id", AsyncMock(return_value=credential)),
        patch(
            "antcode_core.application.services.projects.git_credential_service.secret_box.decrypt",
            return_value="token-123",
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await service.build_auth_config(
                "https://github.com/openai/antcode.git",
                "cred-001",
            )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Git 凭证 host_scope 不匹配"
