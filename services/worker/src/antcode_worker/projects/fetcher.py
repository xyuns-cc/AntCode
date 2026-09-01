"""
Source bundle 项目获取。

Worker 只接受 pgartifact://<sha256>，从 PostgreSQL artifact blob 读取源码包，
校验 sha256 后解包到当前 run 的临时工作区。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shutil
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from antcode_worker.artifact_transfer import SourceBundleDownload
from antcode_worker.projects.workspace_gc import cleanup_stale_workspaces, remove_run_workspace

PGARTIFACT_SCHEME = "pgartifact"
SHA256_RE = re.compile(r"^[a-fA-F0-9]{64}$")
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 10_000
MAX_MEMBER_BYTES = 64 * 1024 * 1024
MAX_EXTRACTED_BYTES = 256 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100


def _safe_slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]", "_", value)


class ProjectWorkspace:
    """Per-run source workspace."""

    def __init__(self, root_dir: str):
        self._root_dir = Path(root_dir)
        self._root_dir.mkdir(parents=True, exist_ok=True)
        cleanup_stale_workspaces(self._root_dir)

    def project_dir(self, run_id: str, project_id: str, sha256: str) -> Path:
        return self._root_dir / _safe_slug(run_id) / _safe_slug(project_id) / sha256

    async def cleanup(self, run_id: str) -> None:
        run_dir = self._root_dir / _safe_slug(run_id)
        await asyncio.to_thread(remove_run_workspace, self._root_dir, run_dir.name)


@dataclass(frozen=True)
class FetchedWorkspace:
    """Resolved source bundle workspace paths."""

    bundle_root: str
    project_cwd: str


class ArtifactFetcher:
    """Source bundle 获取器。"""

    def __init__(self, workspace: ProjectWorkspace, artifact_store: Any):
        self._workspace = workspace
        self._artifact_store = artifact_store

    async def fetch(
        self,
        run_id: str,
        project_id: str,
        source_bundle_uri: str,
        source_bundle_sha256: str,
        source_bundle_size: int,
        entry_point: str | None = None,
        source_subdir: str | None = None,
    ) -> FetchedWorkspace:
        del entry_point
        content_hash = self._validate_source_bundle(
            source_bundle_uri,
            source_bundle_sha256,
            source_bundle_size,
        )

        project_dir = self._workspace.project_dir(run_id, project_id, content_hash)
        await asyncio.to_thread(self._prepare_project_dir, project_dir)
        archive_path = project_dir / "source.bundle"
        try:
            await self._artifact_store.download_source_bundle(
                SourceBundleDownload(
                    run_id=run_id,
                    project_id=project_id,
                    content_hash=content_hash,
                    size_bytes=source_bundle_size,
                    max_bytes=MAX_ARCHIVE_BYTES,
                ),
                archive_path,
            )
            await asyncio.to_thread(
                self._verify_bundle_file,
                archive_path,
                source_bundle_sha256,
                source_bundle_size,
            )
            extracted_path = await asyncio.to_thread(self._extract_bundle, archive_path, project_dir)
        except Exception:
            await asyncio.to_thread(shutil.rmtree, project_dir, True)
            raise
        finally:
            archive_path.unlink(missing_ok=True)
        project_cwd = self._resolve_project_cwd(extracted_path, source_subdir)
        return FetchedWorkspace(bundle_root=str(extracted_path), project_cwd=str(project_cwd))

    async def cleanup(self, run_id: str) -> None:
        await self._workspace.cleanup(run_id)

    def _validate_source_bundle(
        self,
        uri: str,
        sha256: str,
        size: int,
    ) -> str:
        if uri.startswith("git+"):
            raise ValueError("Worker 只接受 pgartifact://<sha256> source bundle")
        if not SHA256_RE.fullmatch(sha256):
            raise ValueError("source_bundle_sha256 必须是 64 位十六进制 sha256")
        if size < 0:
            raise ValueError("source_bundle_size 不能为负数")
        if size > MAX_ARCHIVE_BYTES:
            raise ValueError(f"source bundle 超过压缩包上限: {size} > {MAX_ARCHIVE_BYTES}")

        parsed = urlparse(uri)
        if parsed.scheme != PGARTIFACT_SCHEME:
            raise ValueError("Worker 只接受 pgartifact://<sha256> source bundle")
        if parsed.path or parsed.params or parsed.query or parsed.fragment:
            raise ValueError("pgartifact URI 不允许 path/query/fragment")
        if parsed.username or parsed.password or parsed.port:
            raise ValueError("pgartifact URI 只能包含 sha256 主机名")

        content_hash = parsed.hostname or ""
        if not SHA256_RE.fullmatch(content_hash):
            raise ValueError("pgartifact URI 必须是 pgartifact://<sha256>")
        if content_hash.lower() != sha256.lower():
            raise ValueError("pgartifact URI sha256 与 source_bundle_sha256 不一致")
        return content_hash.lower()

    def _verify_bundle_file(self, path: Path, expected_sha256: str, expected_size: int) -> None:
        actual_size = path.stat().st_size
        if actual_size > MAX_ARCHIVE_BYTES:
            raise ValueError(f"source bundle 超过压缩包上限: {actual_size} > {MAX_ARCHIVE_BYTES}")
        if actual_size != expected_size:
            raise ValueError(f"source bundle 大小不一致: expected={expected_size}, actual={actual_size}")
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        actual = digest.hexdigest()
        if actual.lower() != expected_sha256.lower():
            raise ValueError(f"source bundle sha256 不一致: expected={expected_sha256}, actual={actual}")

    def _prepare_project_dir(self, project_dir: Path) -> None:
        if project_dir.exists():
            shutil.rmtree(project_dir)
        project_dir.mkdir(parents=True, exist_ok=True)

    def _extract_bundle(self, archive_path: Path, project_dir: Path) -> Path:
        extract_dir = project_dir / "extracted"
        extract_dir.mkdir(parents=True, exist_ok=True)

        if zipfile.is_zipfile(archive_path):
            self._safe_extract_zip(archive_path, extract_dir)
            return extract_dir
        if self._is_tar_file(archive_path):
            self._safe_extract_tar(archive_path, extract_dir)
            return extract_dir

        raise ValueError("source bundle 必须是 zip 或 tar 压缩包")

    def _is_tar_file(self, archive_path: Path) -> bool:
        try:
            with tarfile.open(archive_path, mode="r:*"):
                return True
        except tarfile.TarError:
            return False

    def _safe_extract_zip(self, archive_path: Path, dest: Path) -> None:
        base_dir = dest.resolve()
        archive_size = archive_path.stat().st_size
        with zipfile.ZipFile(archive_path, "r") as archive:
            members = archive.infolist()
            self._validate_member_count(len(members))
            total = 0
            for member in members:
                if self._is_unsafe_zip_member(member, base_dir):
                    raise ValueError(f"不安全的压缩路径: {member.filename}")
                if member.file_size > MAX_MEMBER_BYTES:
                    raise ValueError(f"压缩包单文件过大: {member.filename}")
                total += member.file_size
                self._validate_extracted_total(total, archive_size)
                if member.is_dir():
                    (base_dir / member.filename).mkdir(parents=True, exist_ok=True)
                    continue
                target = (base_dir / member.filename).resolve()
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member, "r") as source, open(target, "wb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)

    def _safe_extract_tar(self, archive_path: Path, dest: Path) -> None:
        base_dir = dest.resolve()
        archive_size = archive_path.stat().st_size
        with tarfile.open(archive_path, mode="r:*") as archive:
            members = archive.getmembers()
            self._validate_member_count(len(members))
            total = 0
            for member in members:
                if self._is_unsafe_tar_member(member, base_dir):
                    raise ValueError(f"不安全的压缩路径: {member.name}")
                if member.size > MAX_MEMBER_BYTES:
                    raise ValueError(f"压缩包单文件过大: {member.name}")
                total += member.size
                self._validate_extracted_total(total, archive_size)
                target = (base_dir / member.name).resolve()
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError(f"无法读取压缩包成员: {member.name}")
                with source, open(target, "wb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)

    @staticmethod
    def _validate_member_count(count: int) -> None:
        if count > MAX_ARCHIVE_MEMBERS:
            raise ValueError(f"压缩包成员数超过上限: {count} > {MAX_ARCHIVE_MEMBERS}")

    @staticmethod
    def _validate_extracted_total(total: int, archive_size: int) -> None:
        if total > MAX_EXTRACTED_BYTES:
            raise ValueError(f"解压总量超过上限: {total} > {MAX_EXTRACTED_BYTES}")
        if archive_size > 0 and total > archive_size * MAX_COMPRESSION_RATIO:
            raise ValueError(f"压缩比超过上限: extracted={total}, archive={archive_size}")

    def _resolve_project_cwd(self, bundle_root: Path, source_subdir: str | None) -> Path:
        relative = self._normalize_subdir(source_subdir)
        project_cwd = (bundle_root / relative).resolve()
        base_dir = bundle_root.resolve()
        if os.path.commonpath([str(base_dir), str(project_cwd)]) != str(base_dir):
            raise ValueError("source_subdir 越界")
        if not project_cwd.is_dir():
            raise ValueError(f"source_subdir 不存在: {relative}")
        return project_cwd

    def _normalize_subdir(self, source_subdir: str | None) -> str:
        normalized = (source_subdir or "").strip().replace("\\", "/").strip("/")
        if not normalized or normalized == ".":
            raise ValueError("source_subdir 不能为空")
        parts = normalized.split("/")
        if any(part in {"..", ".git"} for part in parts):
            raise ValueError("source_subdir 不合法")
        return normalized

    def _is_unsafe_zip_member(self, member: zipfile.ZipInfo, base_dir: Path) -> bool:
        mode = member.external_attr >> 16
        if (mode & 0o120000) == 0o120000:
            return True
        return not self._is_safe_member_path(member.filename, base_dir)

    def _is_unsafe_tar_member(self, member: tarfile.TarInfo, base_dir: Path) -> bool:
        if not (member.isfile() or member.isdir()):
            return True
        return not self._is_safe_member_path(member.name, base_dir)

    def _is_safe_member_path(self, name: str, base_dir: Path) -> bool:
        if not name:
            return False
        try:
            base = base_dir.resolve()
            target = (base / name).resolve()
            return os.path.commonpath([str(base), str(target)]) == str(base)
        except (ValueError, RuntimeError):
            return False
