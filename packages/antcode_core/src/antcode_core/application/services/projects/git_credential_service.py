"""Git 凭证服务。"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from urllib.parse import urlparse

from fastapi import HTTPException, status

from antcode_core.common.security.secret_box import secret_box
from antcode_core.domain.models import GitCredential


@dataclass(frozen=True)
class GitAuthConfig:
    """Git 命令鉴权配置。"""

    header_value: str
    credential_public_id: str


class GitCredentialService:
    """Git 凭证读写与鉴权装配。"""

    async def list_for_user(self, user_id: int) -> list[GitCredential]:
        return await GitCredential.filter(owner_user_id=user_id).order_by("-updated_at").all()

    async def get_for_user(self, credential_id: str, user_id: int) -> GitCredential | None:
        return await GitCredential.get_or_none(public_id=credential_id, owner_user_id=user_id)

    async def get_by_public_id(self, credential_id: str) -> GitCredential | None:
        return await GitCredential.get_or_none(public_id=credential_id)

    async def create_for_user(self, user_id: int, payload) -> GitCredential:
        username = self._normalize_username(payload.username)
        self._validate_username_requirement(payload.auth_type, username)
        return await GitCredential.create(
            name=payload.name.strip(),
            auth_type=payload.auth_type,
            username=username,
            secret_encrypted=secret_box.encrypt(payload.secret),
            host_scope=payload.host_scope,
            owner_user_id=user_id,
        )

    async def update_for_user(self, credential_id: str, user_id: int, payload) -> GitCredential | None:
        credential = await self.get_for_user(credential_id, user_id)
        if credential is None:
            return None

        auth_type = payload.auth_type or credential.auth_type
        username = credential.username
        if payload.username is not None:
            username = self._normalize_username(payload.username)
        self._validate_username_requirement(auth_type, username)

        if payload.name is not None:
            credential.name = payload.name.strip()
        if payload.auth_type is not None:
            credential.auth_type = payload.auth_type
        if payload.username is not None:
            credential.username = username
        if payload.host_scope is not None:
            credential.host_scope = payload.host_scope
        if payload.secret is not None:
            credential.secret_encrypted = secret_box.encrypt(payload.secret)
        await credential.save()
        return credential

    async def delete_for_user(self, credential_id: str, user_id: int) -> bool:
        credential = await self.get_for_user(credential_id, user_id)
        if credential is None:
            return False
        await credential.delete()
        return True

    async def ensure_accessible(self, credential_id: str | None, user_id: int) -> None:
        if not credential_id:
            return
        credential = await self.get_for_user(credential_id, user_id)
        if credential is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Git 凭证不存在")

    async def build_auth_config(
        self,
        git_url: str,
        credential_id: str | None,
    ) -> GitAuthConfig | None:
        if not credential_id:
            return None
        credential = await self.get_by_public_id(credential_id)
        if credential is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Git 凭证不存在")
        self._validate_host_scope(git_url, credential.host_scope)
        return GitAuthConfig(
            header_value=self._build_authorization_header(credential),
            credential_public_id=credential.public_id,
        )

    def _build_authorization_header(self, credential: GitCredential) -> str:
        secret = secret_box.decrypt(credential.secret_encrypted)
        if credential.auth_type == "basic":
            return self._build_basic_header(credential.username, secret)
        return f"Authorization: Bearer {secret}"

    def _build_basic_header(self, username: str | None, secret: str) -> str:
        if not username:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Basic 凭证缺少 username")
        token = base64.b64encode(f"{username}:{secret}".encode("utf-8")).decode("utf-8")
        return f"Authorization: Basic {token}"

    def _validate_host_scope(self, git_url: str, host_scope: str) -> None:
        host = (urlparse(git_url).hostname or "").lower()
        scope = host_scope.strip().lower()
        if not host or not scope:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Git 地址或 host_scope 无效")
        if host == scope or host.endswith(f".{scope}"):
            return
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Git 凭证 host_scope 不匹配")

    def _normalize_username(self, username: str | None) -> str | None:
        if username is None:
            return None
        normalized = username.strip()
        return normalized or None

    def _validate_username_requirement(self, auth_type: str, username: str | None) -> None:
        if auth_type == "basic" and not username:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Basic 凭证必须提供 username",
            )


git_credential_service = GitCredentialService()


__all__ = ["GitAuthConfig", "GitCredentialService", "git_credential_service"]
