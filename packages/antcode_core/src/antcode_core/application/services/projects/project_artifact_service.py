"""后端项目产物物化服务。"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from fastapi import HTTPException, status

from antcode_core.application.services.projects.artifact_tree import (
    copy_materialized_tree,
    zip_materialized_tree,
)
from antcode_core.application.services.projects.git_credential_service import (
    GitAuthConfig,
    git_credential_service,
)
from antcode_core.application.services.projects.managed_paths import (
    build_managed_path,
    get_managed_root,
    resolve_managed_path,
)
from antcode_core.common.hash_utils import calculate_content_hash, calculate_file_hash


@dataclass(frozen=True)
class MaterializedArtifact:
    file_path: str
    original_file_path: str
    original_name: str
    file_hash: str
    file_size: int
    file_type: str = ".zip"
    is_compressed: bool = True
    resolved_revision: str = ""

    def to_transfer_info(self, entry_point: str | None) -> dict[str, object]:
        data = asdict(self)
        data["entry_point"] = entry_point or ""
        data["transfer_method"] = "managed_archive"
        return data


class ProjectArtifactService:
    """后端项目产物物化服务。"""

    def __init__(self) -> None:
        self._git_locks: dict[str, asyncio.Lock] = {}
        self._git_lock_guard = asyncio.Lock()

    async def materialize_inline_code(
        self,
        project_public_id: str,
        entry_point: str,
        content: str,
    ) -> MaterializedArtifact:
        normalized_entry = _normalize_relative_path(entry_point)
        content_revision = calculate_content_hash(content, "md5")
        artifact_key = _build_inline_artifact_key(normalized_entry, content_revision)
        workspace_path = build_managed_path("inline", project_public_id, artifact_key, "workspace")
        archive_path = build_managed_path("inline", project_public_id, artifact_key, "artifact.zip")
        await asyncio.to_thread(
            self._ensure_inline_artifact,
            workspace_path,
            archive_path,
            normalized_entry,
            content,
        )
        return _build_artifact_result(
            archive_path=archive_path,
            workspace_path=workspace_path,
            resolved_revision=content_revision,
        )

    async def materialize_git_source(
        self,
        project_public_id: str,
        source_config: dict[str, str],
    ) -> MaterializedArtifact:
        auth_config = await git_credential_service.build_auth_config(
            source_config["url"],
            source_config.get("git_credential_id"),
        )
        lock = await self._get_git_lock(source_config)
        async with lock:
            resolved_revision = source_config.get("commit") or await asyncio.to_thread(
                self._resolve_git_revision,
                source_config,
                auth_config,
            )
            artifact_key = _build_git_artifact_key(source_config, resolved_revision)
            workspace_path = build_managed_path("git", project_public_id, artifact_key, "workspace")
            archive_path = build_managed_path("git", project_public_id, artifact_key, "artifact.zip")
            await asyncio.to_thread(
                self._ensure_git_artifact,
                workspace_path,
                archive_path,
                source_config,
                resolved_revision,
                auth_config,
            )
        return _build_artifact_result(
            archive_path=archive_path,
            workspace_path=workspace_path,
            resolved_revision=resolved_revision,
        )

    async def _get_git_lock(self, source_config: dict[str, str]) -> asyncio.Lock:
        key_parts = [
            source_config.get("url", ""),
            source_config.get("commit") or source_config.get("branch") or "HEAD",
            source_config.get("subdir", ""),
            source_config.get("git_credential_id", ""),
        ]
        lock_key = "|".join(key_parts)
        async with self._git_lock_guard:
            lock = self._git_locks.get(lock_key)
            if lock is None:
                lock = asyncio.Lock()
                self._git_locks[lock_key] = lock
            return lock

    def _ensure_inline_artifact(
        self,
        workspace_path: str,
        archive_path: str,
        entry_point: str,
        content: str,
    ) -> None:
        archive_abs = Path(resolve_managed_path(archive_path))
        workspace_abs = Path(resolve_managed_path(workspace_path))
        if archive_abs.exists() and workspace_abs.exists():
            return
        shutil.rmtree(workspace_abs.parent, ignore_errors=True)
        target_file = workspace_abs / entry_point
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text(content, encoding="utf-8")
        zip_materialized_tree(workspace_abs, archive_abs)

    def _ensure_git_artifact(
        self,
        workspace_path: str,
        archive_path: str,
        source_config: dict[str, str],
        revision: str,
        auth_config: GitAuthConfig | None,
    ) -> None:
        archive_abs = Path(resolve_managed_path(archive_path))
        workspace_abs = Path(resolve_managed_path(workspace_path))
        if archive_abs.exists() and workspace_abs.exists():
            return
        shutil.rmtree(workspace_abs.parent, ignore_errors=True)
        with tempfile.TemporaryDirectory(dir=get_managed_root()) as temp_dir:
            repo_dir = Path(temp_dir) / "repo"
            _clone_repo(repo_dir, source_config, revision, auth_config)
            source_root = _resolve_source_root(repo_dir, source_config.get("subdir"))
            copy_materialized_tree(source_root, workspace_abs)
            zip_materialized_tree(workspace_abs, archive_abs)

    def _resolve_git_revision(
        self,
        source_config: dict[str, str],
        auth_config: GitAuthConfig | None,
    ) -> str:
        ref = source_config.get("branch") or "HEAD"
        result = _run_git(
            ["git", "ls-remote", source_config["url"], ref],
            auth_config=auth_config,
        )
        lines = result.stdout.strip().splitlines()
        if not lines:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="无法解析 Git 引用版本",
            )
        return lines[0].split()[0]


def _build_artifact_result(
    *,
    archive_path: str,
    workspace_path: str,
    resolved_revision: str,
) -> MaterializedArtifact:
    archive_abs = Path(resolve_managed_path(archive_path))
    return MaterializedArtifact(
        file_path=workspace_path,
        original_file_path=archive_path,
        original_name=archive_abs.name,
        file_hash=calculate_file_hash(archive_abs, "md5"),
        file_size=archive_abs.stat().st_size,
        resolved_revision=resolved_revision,
    )


def _build_inline_artifact_key(entry_point: str, content_revision: str) -> str:
    return calculate_content_hash(f"{entry_point}\n{content_revision}", "md5")


def _build_git_artifact_key(source_config: dict[str, str], resolved_revision: str) -> str:
    key_parts = [
        source_config.get("url", ""),
        resolved_revision,
        source_config.get("subdir", ""),
    ]
    return calculate_content_hash("\n".join(key_parts), "md5")


def _normalize_relative_path(path: str) -> str:
    normalized = path.strip().replace("\\", "/").strip("/")
    if not normalized or normalized == ".":
        raise ValueError("入口路径不能为空")
    if ".." in normalized.split("/"):
        raise ValueError("入口路径不合法")
    return normalized


def _clone_repo(
    repo_dir: Path,
    source_config: dict[str, str],
    revision: str,
    auth_config: GitAuthConfig | None,
) -> None:
    command = ["git", "clone"]
    branch = source_config.get("branch")
    if branch and not source_config.get("commit"):
        command.extend(["--depth", "1", "--branch", branch])
    command.extend([source_config["url"], str(repo_dir)])
    _run_git(command, auth_config=auth_config)
    if source_config.get("commit"):
        _run_git(["git", "checkout", revision], cwd=repo_dir, auth_config=auth_config)


def _resolve_source_root(repo_dir: Path, subdir: str | None) -> Path:
    if not subdir:
        return repo_dir
    normalized = _normalize_relative_path(subdir)
    target = (repo_dir / normalized).resolve()
    root = repo_dir.resolve()
    if os.path.commonpath([str(root), str(target)]) != str(root):
        raise ValueError("Git 子目录越界")
    if not target.exists():
        raise FileNotFoundError(f"Git 子目录不存在: {normalized}")
    return target


def _run_git(
    command: list[str],
    cwd: Path | None = None,
    auth_config: GitAuthConfig | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=_build_git_env(auth_config),
        check=True,
        capture_output=True,
        text=True,
    )


def _build_git_env(auth_config: GitAuthConfig | None) -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    if auth_config is None:
        return env
    env["GIT_CONFIG_COUNT"] = "1"
    env["GIT_CONFIG_KEY_0"] = "http.extraHeader"
    env["GIT_CONFIG_VALUE_0"] = auth_config.header_value
    return env
project_artifact_service = ProjectArtifactService()
