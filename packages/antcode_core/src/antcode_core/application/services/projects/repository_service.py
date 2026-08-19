"""Git repository resource service."""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from tortoise.transactions import in_transaction

from antcode_core.application.services.projects.git_credential_service import (
    git_credential_service,
)
from antcode_core.application.services.projects.git_transfer_quota import TransferBudget
from antcode_core.application.services.projects.source_bundle_paths import (
    RepositoryScanLimits,
    scan_repository_candidates,
)
from antcode_core.application.services.projects.source_bundle_service import (
    _clone_repo,
    _resolve_git_revision,
)
from antcode_core.application.services.users.user_ownership_lock import lock_user
from antcode_core.common.config import settings
from antcode_core.common.runtime_paths import ensure_runtime_dir
from antcode_core.domain.models import GitRepository, ProjectSource


class RepositoryDeleteStatus(StrEnum):
    DELETED = "deleted"
    IN_USE = "in_use"
    NOT_FOUND = "not_found"


class RepositoryScanError(Exception):
    """扫描失败的原因出在仓库配置本身，用户可以自己改。

    ``ValueError`` 来自 ``_resolve_git_revision``（ref 不存在、URL 非法、ref 数超限），
    ``SubprocessError`` 来自 git 子进程（远端不可达、鉴权失败、超时）。这两类必须把
    原因回给用户；此前它们直接逃逸成 500「服务器内部错误」，用户填错分支名只会看到
    一句和自己的输入毫无关系的话。其余异常（真缺陷）继续逃逸，不伪装成用户输入错误。
    """


_SCAN_CONFIG_ERRORS = (ValueError, subprocess.SubprocessError)


class RepositoryService:
    """CRUD and scanning for Git repositories."""

    async def list_for_user(self, user_id: int) -> list[GitRepository]:
        return await GitRepository.filter(owner_user_id=user_id).order_by("-updated_at")

    async def get_for_user(self, repository_id: str, user_id: int) -> GitRepository | None:
        return await GitRepository.get_or_none(public_id=repository_id, owner_user_id=user_id)

    async def create_for_user(self, user_id: int, payload) -> GitRepository:
        await git_credential_service.ensure_accessible(payload.credential_id, user_id)
        async with in_transaction("default") as connection:
            await lock_user(connection, user_id, active_only=True)
            return await GitRepository.create(
                name=payload.name.strip(),
                url=payload.url.strip(),
                default_ref=payload.default_ref.strip() or "main",
                credential_id=payload.credential_id,
                owner_user_id=user_id,
                using_db=connection,
            )

    async def update_for_user(self, repository_id: str, user_id: int, payload) -> GitRepository | None:
        repository = await self.get_for_user(repository_id, user_id)
        if repository is None:
            return None
        await self._apply_update(repository, user_id, payload)
        await repository.save()
        return repository

    async def delete_for_user(self, repository_id: str, user_id: int) -> RepositoryDeleteStatus:
        async with in_transaction("default") as connection:
            repository = (
                await GitRepository.filter(public_id=repository_id, owner_user_id=user_id)
                .using_db(connection)
                .select_for_update()
                .first()
            )
            if repository is None:
                return RepositoryDeleteStatus.NOT_FOUND
            in_use = await ProjectSource.filter(repository_id=repository.id).using_db(connection).exists()
            if in_use:
                return RepositoryDeleteStatus.IN_USE
            await repository.delete(using_db=connection)
        return RepositoryDeleteStatus.DELETED

    async def scan_for_user(
        self,
        repository_id: str,
        user_id: int,
        ref: str | None = None,
    ) -> tuple[GitRepository | None, list[dict[str, object]]]:
        repository = await self.get_for_user(repository_id, user_id)
        if repository is None:
            return None, []
        candidates = await self._scan_repository(repository, ref or repository.default_ref)
        return repository, candidates

    async def _apply_update(self, repository, user_id: int, payload) -> None:
        if payload.name is not None:
            repository.name = payload.name.strip()
        if payload.url is not None:
            repository.url = payload.url.strip()
        if payload.default_ref is not None:
            repository.default_ref = payload.default_ref.strip() or "main"
        # credential_id 用 model_fields_set 而非 ``is not None`` 判定：``null`` 是
        # "解绑凭证"这个合法意图，按 ``is not None`` 会与"没传该字段"混为一谈，
        # 前端把凭证清空后请求照样 200，凭证却还挂着——一个静默失败。
        if "credential_id" in payload.model_fields_set:
            await git_credential_service.ensure_accessible(payload.credential_id, user_id)
            repository.credential_id = payload.credential_id
        if payload.enabled is not None:
            repository.enabled = payload.enabled

    async def _scan_repository(self, repository: GitRepository, ref: str) -> list[dict[str, object]]:
        try:
            auth_config = await git_credential_service.build_auth_config(
                repository.url,
                repository.credential_id,
            )
            candidates = await asyncio.to_thread(
                self._clone_and_scan,
                repository,
                ref,
                auth_config,
            )
        except Exception as exc:
            await self._record_scan(repository, "failed", [], str(exc))
            if isinstance(exc, _SCAN_CONFIG_ERRORS):
                # 复用刚落库的脱敏消息，保证接口报错与列表里的 last_scan_error 一字不差
                raise RepositoryScanError(repository.last_scan_error or str(exc)) from exc
            raise
        await self._record_scan(repository, "success", candidates, None)
        return candidates

    async def _record_scan(
        self,
        repository: GitRepository,
        status: str,
        result: list[dict[str, object]],
        error: str | None,
    ) -> None:
        repository.last_scan_status = status
        repository.last_scan_result = result
        from antcode_core.common.error_messages import normalize_persisted_error_message

        repository.last_scan_error = normalize_persisted_error_message(error)
        repository.last_scanned_at = datetime.now()
        await repository.save()

    def _clone_and_scan(
        self,
        repository: GitRepository,
        ref: str,
        auth_config,
    ) -> list[dict[str, object]]:
        source_config = self._source_config(repository, ref)
        transfer_budget = TransferBudget(settings.GIT_MAX_TRANSFER_BYTES)
        revision = _resolve_git_revision(
            source_config,
            auth_config,
            transfer_budget=transfer_budget,
        )
        temp_parent = ensure_runtime_dir("master", "tmp", "repository-scans")
        with tempfile.TemporaryDirectory(dir=temp_parent) as temp_dir:
            repo_dir = Path(temp_dir) / "repo"
            _clone_repo(
                repo_dir,
                source_config,
                revision,
                auth_config=auth_config,
                transfer_budget=transfer_budget,
            )
            return scan_repository_candidates(
                repo_dir,
                RepositoryScanLimits(
                    max_files=settings.GIT_SCAN_MAX_FILES,
                    max_directories=settings.GIT_SCAN_MAX_DIRECTORIES,
                    max_depth=settings.GIT_SCAN_MAX_DEPTH,
                    max_candidates=settings.GIT_SCAN_MAX_CANDIDATES,
                ),
            )

    def _source_config(self, repository: GitRepository, ref: str) -> dict[str, object]:
        return {
            "url": repository.url,
            "ref": ref,
            "branch": ref,
            "credential_id": repository.credential_id,
        }


repository_service = RepositoryService()
