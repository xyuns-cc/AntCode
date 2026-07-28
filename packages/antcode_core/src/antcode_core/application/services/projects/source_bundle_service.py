"""Master-side Git source bundle builder."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from antcode_core.application.services.projects.git_credential_service import (
    GitAuthConfig,
    git_credential_service,
)
from antcode_core.application.services.projects.git_process_limits import (
    GitCommandLimits,
    resolve_with_timeout,
    run_bounded_git_command,
    validate_git_ref,
    validate_repository_metadata,
)
from antcode_core.application.services.projects.git_transfer_quota import TransferBudget
from antcode_core.application.services.projects.git_transport import (
    build_git_env as _build_git_env,
)
from antcode_core.application.services.projects.git_transport import (
    git_transport as _git_transport,
)
from antcode_core.application.services.projects.git_url_security import ResolvedURL
from antcode_core.application.services.projects.git_url_security import resolve_git_url as _resolve_git_url
from antcode_core.application.services.projects.source_bundle_metadata import (
    artifact_metadata as _artifact_metadata,
)
from antcode_core.application.services.projects.source_bundle_metadata import optional_str as _optional_str
from antcode_core.application.services.projects.source_bundle_metadata import (
    require_git_source as _require_git_source,
)
from antcode_core.application.services.projects.source_bundle_paths import (
    create_deterministic_tar_gz as _create_deterministic_tar_gz,
)
from antcode_core.application.services.projects.source_bundle_paths import (
    resolve_bundle_paths as _resolve_bundle_paths,
)
from antcode_core.application.services.projects.source_bundle_paths import (
    string_list,
)
from antcode_core.application.services.projects.source_bundle_paths import (
    validate_bundle_paths as _validate_bundle_paths,
)
from antcode_core.common.config import settings
from antcode_core.common.runtime_paths import ensure_runtime_dir
from antcode_core.infrastructure.postgres.artifact_store import PostgresArtifactStore

SOURCE_BUNDLE_MEDIA_TYPE = "application/vnd.antcode.source-bundle+tar-gzip"


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
        transfer_budget = TransferBudget(settings.GIT_MAX_TRANSFER_BYTES)
        resolved_revision = _optional_str(source_config.get("commit"))
        if not resolved_revision:
            resolved_revision = await asyncio.to_thread(
                _resolve_git_revision,
                source_config,
                auth_config,
                transfer_budget=transfer_budget,
            )
        content = await asyncio.to_thread(
            _materialize_bundle,
            source_config,
            resolved_revision,
            auth_config,
            entry_point=entry_point,
            transfer_budget=transfer_budget,
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
    *,
    entry_point: str | None = None,
    transfer_budget: TransferBudget,
) -> bytes:
    temp_parent = ensure_runtime_dir("master", "tmp", "source-bundles")
    with tempfile.TemporaryDirectory(dir=temp_parent) as temp_dir:
        repo_dir = Path(temp_dir) / "repo"
        _clone_repo(
            repo_dir,
            source_config,
            revision,
            auth_config=auth_config,
            transfer_budget=transfer_budget,
        )
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


def _resolve_git_revision(
    source_config: dict[str, object],
    auth_config,
    *,
    transfer_budget: TransferBudget,
) -> str:
    ref = _optional_str(source_config.get("ref"))
    ref = ref or _optional_str(source_config.get("branch")) or "HEAD"
    validate_git_ref(ref)
    endpoint = _resolve_git_endpoint(str(source_config["url"]))
    refs = _run_git(
        ["git", "ls-remote", "--refs", "--", endpoint.url],
        auth_config=auth_config,
        endpoint=endpoint,
        transfer_budget=transfer_budget,
    ).stdout.splitlines()
    if len(refs) > settings.GIT_MAX_REFS:
        raise ValueError(f"Git 远端 ref 数超过上限 {settings.GIT_MAX_REFS}")
    # ``--`` 阻止 git 把 URL/ref 解析成 flag（防御纵深）。
    # 额外带上 ``<ref>^{}`` pattern：ls-remote 的 pattern 是尾部匹配，
    # 单独查 ``<ref>`` 不会返回 annotated tag 的 ``^{}`` 剥离行，
    # 必须显式请求才能拿到 tag 指向的 commit。
    result = _run_git(
        ["git", "ls-remote", "--", endpoint.url, ref, f"{ref}^{{}}"],
        auth_config=auth_config,
        endpoint=endpoint,
        transfer_budget=transfer_budget,
    )
    entries = _parse_ls_remote_output(result.stdout)
    if not entries:
        raise ValueError("无法解析 Git 引用版本")
    return _select_revision(entries, ref)


def _parse_ls_remote_output(output: str) -> list[tuple[str, str]]:
    """把 ``git ls-remote`` 输出解析成 (sha, refname) 列表。"""
    entries: list[tuple[str, str]] = []
    for line in output.strip().splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            continue
        entries.append((parts[0].strip(), parts[1].strip()))
    return entries


def _select_revision(entries: list[tuple[str, str]], ref: str) -> str:
    """从 ls-remote 结果中确定性地选出目标 commit。

    必须与 ``_clone_repo`` 的 ``--branch <ref>`` 语义保持一致：同名
    branch/tag 并存时 git clone 会解析成 branch，因此这里优先精确匹配
    ``refs/heads/<ref>``；其次 ``refs/tags/<ref>``（annotated tag 取
    ``^{}`` 剥离后的 commit）。否则会解析出 clone 拿不到的 tag commit，
    导致 checkout 失败或 "检出版本不一致" 报错。
    """
    by_ref = {refname: sha for sha, refname in entries}
    branch_sha = by_ref.get(f"refs/heads/{ref}")
    if branch_sha:
        return branch_sha
    tag_ref = f"refs/tags/{ref}"
    tag_sha = by_ref.get(tag_ref)
    if tag_sha:
        # ``git clone --no-tags --branch <tag>`` 在无同名 branch 时会
        # 直接以 detached HEAD 检出该 tag 的 commit，因此返回剥离后的
        # commit 与后续 checkout / rev-parse 校验一致。
        return by_ref.get(f"{tag_ref}^{{}}") or tag_sha
    # 其他形态（HEAD、完整 ref 路径、SHA 前缀等）：保留原有行为，
    # 优先 ^{} 剥离行（annotated tag → commit），否则取最后一行。
    dereferenced = [sha for sha, refname in entries if refname.endswith("^{}")]
    if dereferenced:
        return dereferenced[-1]
    return entries[-1][0]


def _clone_repo(
    repo_dir: Path,
    source_config: dict[str, object],
    revision: str,
    *,
    auth_config: GitAuthConfig | None,
    transfer_budget: TransferBudget,
) -> None:
    endpoint = _resolve_git_endpoint(str(source_config["url"]))
    if shutil.disk_usage(repo_dir.parent).free < settings.GIT_MAX_REPOSITORY_BYTES:
        raise OSError(f"Git 临时目录可用空间低于配额 {settings.GIT_MAX_REPOSITORY_BYTES} 字节")
    command = [
        "git",
        "clone",
        "--quiet",
        "--no-tags",
        "--no-checkout",
        "--no-recurse-submodules",
        f"--filter=blob:limit={settings.GIT_MAX_BLOB_BYTES}",
    ]
    branch = _optional_str(source_config.get("branch")) or _optional_str(source_config.get("ref"))
    if branch and not source_config.get("commit"):
        command.extend(["--depth", "1", "--single-branch", "--branch", branch])
    # ``--`` 阻止 git 把 URL 解析成 flag
    command.extend(["--", endpoint.url, str(repo_dir)])
    _run_git(
        command,
        auth_config=auth_config,
        endpoint=endpoint,
        quota_path=repo_dir,
        transfer_budget=transfer_budget,
    )
    # revision 已由 _resolve_git_revision 解析并校验为 40 位十六进制 SHA，无注入面；
    # checkout 不能加 ``--``（其后被 git 当作 pathspec 而非 ref，会破坏 detach 语义）。
    _run_git(
        ["git", "checkout", "--detach", revision],
        cwd=repo_dir,
        auth_config=auth_config,
        endpoint=endpoint,
        quota_path=repo_dir,
        transfer_budget=transfer_budget,
    )
    actual_revision = _run_git(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_dir,
        auth_config=auth_config,
        transfer_budget=transfer_budget,
    ).stdout.strip()
    if actual_revision != revision:
        raise ValueError(f"Git 检出版本不一致: expected={revision} actual={actual_revision}")
    validate_repository_metadata(
        repo_dir,
        lambda command, **kwargs: _run_git(
            command,
            auth_config=auth_config,
            transfer_budget=transfer_budget,
            **kwargs,
        ),
        max_objects=settings.GIT_MAX_OBJECTS,
        max_refs=settings.GIT_MAX_REFS,
    )


def _run_git(
    command: list[str],
    *,
    cwd: Path | None = None,
    auth_config: GitAuthConfig | None = None,
    timeout: float | None = None,
    endpoint: ResolvedURL | None = None,
    quota_path: Path | None = None,
    transfer_budget: TransferBudget,
) -> subprocess.CompletedProcess[str]:
    # P1-11: 无 timeout 的 git ls-remote / clone 可被 slow-loris / 巨型 repo 无限拖住 master。
    # 默认按命令类型分配:ls-remote 短超时,clone 长超时。
    if timeout is None:
        if command and len(command) >= 2 and command[1] == "ls-remote":
            timeout = settings.GIT_LS_REMOTE_TIMEOUT_SECONDS
        else:
            timeout = settings.GIT_CLONE_TIMEOUT_SECONDS
    with _git_transport(endpoint, transfer_budget) as transport:
        return run_bounded_git_command(
            command,
            cwd=cwd,
            env=_build_git_env(auth_config, endpoint, transport),
            limits=GitCommandLimits(
                timeout_seconds=timeout,
                max_output_bytes=settings.GIT_MAX_COMMAND_OUTPUT_BYTES,
                max_repository_bytes=settings.GIT_MAX_REPOSITORY_BYTES,
            ),
            quota_path=quota_path,
            failure_probe=transport.failure,
        )


def _resolve_git_endpoint(url: str) -> ResolvedURL:
    endpoint = resolve_with_timeout(_resolve_git_url, url, settings.GIT_DNS_TIMEOUT_SECONDS)
    if not isinstance(endpoint, ResolvedURL):
        raise TypeError("Git URL resolver returned an invalid endpoint")
    return endpoint


source_bundle_service = SourceBundleService()
