"""Master-side Git source bundle builder."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from antcode_core.application.services.projects.git_credential_service import (
    GitAuthConfig,
    git_credential_service,
)
from antcode_core.application.services.projects.git_url_security import validate_git_url as _validate_git_url
from antcode_core.application.services.projects.source_bundle_paths import (
    create_deterministic_tar_gz as _create_deterministic_tar_gz,
)
from antcode_core.application.services.projects.source_bundle_paths import (
    resolve_bundle_paths as _resolve_bundle_paths,
)
from antcode_core.application.services.projects.source_bundle_paths import (
    string_list,
)
from antcode_core.common.runtime_paths import ensure_runtime_dir
from antcode_core.infrastructure.postgres.artifact_store import PostgresArtifactStore

SOURCE_BUNDLE_MEDIA_TYPE = "application/vnd.antcode.source-bundle+tar-gzip"


# P1-11: Git 操作 timeout,防止恶意 slow-loris / 大 repo 拖死 master。
# ls-remote 快速探测(30s);clone 主操作(300s = 5 分钟,大 repo 应改 shallow)。
_GIT_LS_REMOTE_TIMEOUT_SEC = 30
_GIT_CLONE_TIMEOUT_SEC = 300
MAX_BUNDLE_FILE_COUNT = 10_000
MAX_BUNDLE_FILE_BYTES = 64 * 1024 * 1024
MAX_BUNDLE_TOTAL_BYTES = 256 * 1024 * 1024
MAX_BUNDLE_ARCHIVE_BYTES = 100 * 1024 * 1024


@dataclass(frozen=True)
class SourceBundle:
    uri: str
    sha256: str
    size_bytes: int
    entry_point: str
    resolved_revision: str
    artifact_id: int


class SourceBundleService:
    """Creates immutable source bundles from Git repositories."""

    def __init__(self, artifact_store: PostgresArtifactStore | None = None) -> None:
        self._artifact_store = artifact_store or PostgresArtifactStore()

    async def create_git_source_bundle(
        self,
        *,
        project_public_id: str,
        source_config: dict[str, object],
        entry_point: str | None,
    ) -> SourceBundle:
        del project_public_id
        _require_git_source(source_config)
        auth_config = await git_credential_service.build_auth_config(
            str(source_config["url"]),
            _optional_str(source_config.get("credential_id")),
        )
        resolved_revision = _optional_str(source_config.get("commit"))
        if not resolved_revision:
            resolved_revision = await asyncio.to_thread(_resolve_git_revision, source_config, auth_config)
        content = await asyncio.to_thread(
            _materialize_bundle,
            source_config,
            resolved_revision,
            auth_config,
            entry_point,
        )
        artifact = await self._artifact_store.write_blob(
            content,
            media_type=SOURCE_BUNDLE_MEDIA_TYPE,
            metadata=_artifact_metadata(source_config, resolved_revision),
        )
        return SourceBundle(
            uri=artifact.uri,
            sha256=artifact.content_hash,
            size_bytes=artifact.size_bytes,
            entry_point=entry_point or "",
            resolved_revision=resolved_revision,
            artifact_id=artifact.artifact_id,
        )


def _materialize_bundle(
    source_config: dict[str, object],
    revision: str,
    auth_config: GitAuthConfig | None,
    entry_point: str | None = None,
) -> bytes:
    temp_parent = ensure_runtime_dir("master", "tmp", "source-bundles")
    with tempfile.TemporaryDirectory(dir=temp_parent) as temp_dir:
        repo_dir = Path(temp_dir) / "repo"
        _clone_repo(repo_dir, source_config, revision, auth_config)
        bundle_paths = _resolve_bundle_paths(
            repo_dir,
            subdir=_optional_str(source_config.get("subdir")),
            entry_point=entry_point,
            include_paths=string_list(source_config.get("include_paths")),
        )
        _validate_bundle_paths(bundle_paths)
        content = _create_deterministic_tar_gz(repo_dir, bundle_paths)
        if len(content) > MAX_BUNDLE_ARCHIVE_BYTES:
            raise ValueError(f"source bundle 压缩包超过上限: {len(content)} > {MAX_BUNDLE_ARCHIVE_BYTES}")
        return content


def _validate_bundle_paths(paths: list[Path]) -> None:
    if len(paths) > MAX_BUNDLE_FILE_COUNT:
        raise ValueError(f"source bundle 文件数超过上限: {len(paths)} > {MAX_BUNDLE_FILE_COUNT}")
    total = 0
    for path in paths:
        size = path.stat().st_size
        if size > MAX_BUNDLE_FILE_BYTES:
            raise ValueError(f"source bundle 单文件超过上限: {path.name} {size} > {MAX_BUNDLE_FILE_BYTES}")
        total += size
        if total > MAX_BUNDLE_TOTAL_BYTES:
            raise ValueError(f"source bundle 总大小超过上限: {total} > {MAX_BUNDLE_TOTAL_BYTES}")


def _resolve_git_revision(source_config: dict[str, object], auth_config) -> str:
    ref = _optional_str(source_config.get("ref"))
    ref = ref or _optional_str(source_config.get("branch")) or "HEAD"
    url = _validate_git_url(str(source_config["url"]))
    # ``--`` 阻止 git 把 URL/ref 解析成 flag（防御纵深）
    result = _run_git(["git", "ls-remote", "--", url, ref], auth_config=auth_config)
    lines = result.stdout.strip().splitlines()
    if not lines:
        raise ValueError("无法解析 Git 引用版本")
    return lines[0].split()[0]


def _clone_repo(
    repo_dir: Path,
    source_config: dict[str, object],
    revision: str,
    auth_config: GitAuthConfig | None,
) -> None:
    url = _validate_git_url(str(source_config["url"]))
    command = ["git", "clone"]
    branch = _optional_str(source_config.get("branch")) or _optional_str(source_config.get("ref"))
    if branch and not source_config.get("commit"):
        command.extend(["--depth", "1", "--branch", branch])
    # ``--`` 阻止 git 把 URL 解析成 flag
    command.extend(["--", url, str(repo_dir)])
    _run_git(command, auth_config=auth_config)
    if source_config.get("commit"):
        _run_git(["git", "checkout", revision], cwd=repo_dir, auth_config=auth_config)


def _run_git(
    command: list[str],
    cwd: Path | None = None,
    auth_config: GitAuthConfig | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    # P1-11: 无 timeout 的 git ls-remote / clone 可被 slow-loris / 巨型 repo 无限拖住 master。
    # 默认按命令类型分配:ls-remote 短超时,clone 长超时。
    if timeout is None:
        if command and len(command) >= 2 and command[1] == "ls-remote":
            timeout = _GIT_LS_REMOTE_TIMEOUT_SEC
        else:
            timeout = _GIT_CLONE_TIMEOUT_SEC
    return subprocess.run(
        command,
        cwd=cwd,
        env=_build_git_env(auth_config),
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
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


def _artifact_metadata(
    source_config: dict[str, object],
    resolved_revision: str,
) -> dict[str, object]:
    include_paths = string_list(source_config.get("include_paths"))
    return {
        "repository_id": source_config.get("repository_id"),
        "resolved_commit": resolved_revision,
        "source_subdir": source_config.get("subdir"),
        "include_paths_hash": _hash_json(include_paths),
    }


def _hash_json(value: object) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _require_git_source(source_config: dict[str, object]) -> None:
    if not source_config.get("url"):
        raise ValueError("Git URL 不能为空")


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


source_bundle_service = SourceBundleService()
