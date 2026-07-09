"""Git repository resource service."""

from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime
from pathlib import Path

from antcode_core.application.services.projects.git_credential_service import (
    git_credential_service,
)
from antcode_core.application.services.projects.source_bundle_paths import (
    scan_repository_candidates,
)
from antcode_core.application.services.projects.source_bundle_service import (
    _clone_repo,
    _resolve_git_revision,
)
from antcode_core.common.runtime_paths import ensure_runtime_dir
from antcode_core.domain.models import GitRepository


class RepositoryService:
    """CRUD and scanning for Git repositories."""

    async def list_for_user(self, user_id: int) -> list[GitRepository]:
        return await GitRepository.filter(owner_user_id=user_id).order_by("-updated_at")

    async def get_for_user(self, repository_id: str, user_id: int) -> GitRepository | None:
        return await GitRepository.get_or_none(public_id=repository_id, owner_user_id=user_id)

    async def create_for_user(self, user_id: int, payload) -> GitRepository:
        await git_credential_service.ensure_accessible(payload.credential_id, user_id)
        return await GitRepository.create(
            name=payload.name.strip(),
            url=payload.url.strip(),
            default_ref=payload.default_ref.strip() or "main",
            credential_id=payload.credential_id,
            owner_user_id=user_id,
        )

    async def update_for_user(self, repository_id: str, user_id: int, payload) -> GitRepository | None:
        repository = await self.get_for_user(repository_id, user_id)
        if repository is None:
            return None
        await self._apply_update(repository, user_id, payload)
        await repository.save()
        return repository

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
        if payload.credential_id is not None:
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
            await self._record_scan(repository, "success", candidates, None)
            return candidates
        except Exception as exc:
            await self._record_scan(repository, "failed", [], str(exc))
            raise

    async def _record_scan(
        self,
        repository: GitRepository,
        status: str,
        result: list[dict[str, object]],
        error: str | None,
    ) -> None:
        repository.last_scan_status = status
        repository.last_scan_result = result
        repository.last_scan_error = error
        repository.last_scanned_at = datetime.now()
        await repository.save()

    def _clone_and_scan(
        self,
        repository: GitRepository,
        ref: str,
        auth_config,
    ) -> list[dict[str, object]]:
        source_config = self._source_config(repository, ref)
        revision = _resolve_git_revision(source_config, auth_config)
        temp_parent = ensure_runtime_dir("master", "tmp", "repository-scans")
        with tempfile.TemporaryDirectory(dir=temp_parent) as temp_dir:
            repo_dir = Path(temp_dir) / "repo"
            _clone_repo(repo_dir, {**source_config, "commit": revision}, revision, auth_config)
            return scan_repository_candidates(repo_dir)

    def _source_config(self, repository: GitRepository, ref: str) -> dict[str, object]:
        return {
            "url": repository.url,
            "ref": ref,
            "branch": ref,
            "credential_id": repository.credential_id,
        }


repository_service = RepositoryService()
