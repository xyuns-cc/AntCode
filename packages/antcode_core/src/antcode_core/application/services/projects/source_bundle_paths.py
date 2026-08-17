"""Source bundle path validation and archive helpers."""

from __future__ import annotations

import gzip
import io
import os
import tarfile
from dataclasses import dataclass
from pathlib import Path

ENTRYPOINT_CANDIDATES = ("main.py", "spider.py", "crawler.py", "app.py")
PROJECT_MARKERS = ENTRYPOINT_CANDIDATES + ("pyproject.toml", "requirements.txt")
MAX_BUNDLE_FILE_COUNT = 10_000
MAX_BUNDLE_FILE_BYTES = 64 * 1024 * 1024
MAX_BUNDLE_TOTAL_BYTES = 256 * 1024 * 1024
MAX_BUNDLE_INCLUDE_PATHS = 100
MAX_LFS_POINTER_BYTES = 1024
LFS_POINTER_PREFIX = b"version https://git-lfs.github.com/spec/v1\n"


@dataclass(frozen=True)
class RepositoryScanLimits:
    max_files: int
    max_directories: int
    max_depth: int
    max_candidates: int


DEFAULT_REPOSITORY_SCAN_LIMITS = RepositoryScanLimits(
    max_files=100_000,
    max_directories=20_000,
    max_depth=50,
    max_candidates=10_000,
)


def scan_repository_candidates(
    repo_dir: Path,
    limits: RepositoryScanLimits | None = None,
) -> list[dict[str, object]]:
    root = repo_dir.resolve()
    scan_limits = limits or DEFAULT_REPOSITORY_SCAN_LIMITS
    candidates: list[dict[str, object]] = []
    file_count = 0
    directory_count = 0
    for current, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name != ".git")
        relative_path = Path(current).resolve().relative_to(root)
        if len(relative_path.parts) > scan_limits.max_depth:
            raise ValueError(f"Git 仓库目录深度超过上限 {scan_limits.max_depth}")
        directory_count += len(dirnames)
        file_count += len(filenames)
        _validate_scan_counts(file_count, directory_count, scan_limits)
        relative = relative_path.as_posix()
        if relative == ".":
            continue
        entry_point_files = set(filenames).intersection(ENTRYPOINT_CANDIDATES)
        if not entry_point_files:
            continue
        marker_files = sorted(set(filenames).intersection(PROJECT_MARKERS))
        candidates.append(_candidate(relative, marker_files))
        if len(candidates) > scan_limits.max_candidates:
            raise ValueError(f"Git 仓库候选项目数超过上限 {scan_limits.max_candidates}")
    return candidates


def _validate_scan_counts(file_count: int, directory_count: int, limits: RepositoryScanLimits) -> None:
    if file_count > limits.max_files:
        raise ValueError(f"Git 仓库扫描文件数超过上限 {limits.max_files}")
    if directory_count > limits.max_directories:
        raise ValueError(f"Git 仓库扫描目录数超过上限 {limits.max_directories}")


def resolve_bundle_paths(
    repo_dir: Path,
    *,
    subdir: str | None,
    entry_point: str | None,
    include_paths: list[str],
) -> list[Path]:
    root = repo_dir.resolve()
    normalized_subdir = normalize_source_subdir(subdir)
    source_root = resolve_existing_dir(root, normalized_subdir, "Git 子目录")
    normalized_entry = normalize_relative_path(entry_point, field_name="入口文件")
    resolve_entry_point(source_root, normalized_entry)
    roots = [source_root]
    roots.extend(resolve_existing_dir(root, item, "include_paths") for item in include_paths)
    return iter_unique_source_files(root, roots)


def create_deterministic_tar_gz(repo_dir: Path, paths: list[Path] | None = None) -> bytes:
    root = repo_dir.resolve()
    source_paths = paths if paths is not None else iter_unique_source_files(root, [root])
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        for path in source_paths:
            add_file(tar, root, path)
    compressed = io.BytesIO()
    with gzip.GzipFile(fileobj=compressed, mode="wb", mtime=0) as gz:
        gz.write(raw.getvalue())
    return compressed.getvalue()


def validate_bundle_paths(paths: list[Path]) -> None:
    if len(paths) > MAX_BUNDLE_FILE_COUNT:
        raise ValueError(f"source bundle 文件数超过上限: {len(paths)} > {MAX_BUNDLE_FILE_COUNT}")
    total = 0
    for path in paths:
        size = path.stat().st_size
        _reject_lfs_pointer(path, size)
        if size > MAX_BUNDLE_FILE_BYTES:
            raise ValueError(f"source bundle 单文件超过上限: {path.name} {size} > {MAX_BUNDLE_FILE_BYTES}")
        total += size
        if total > MAX_BUNDLE_TOTAL_BYTES:
            raise ValueError(f"source bundle 总大小超过上限: {total} > {MAX_BUNDLE_TOTAL_BYTES}")


def _reject_lfs_pointer(path: Path, size: int) -> None:
    if size > MAX_LFS_POINTER_BYTES:
        return
    content = path.read_bytes().replace(b"\r\n", b"\n")
    if not content.startswith(LFS_POINTER_PREFIX):
        return
    if b"\noid sha256:" in content and b"\nsize " in content:
        raise ValueError(f"Git LFS 文件未物化，拒绝生成不完整 source bundle: {path.name}")


def list_tar_names(content: bytes) -> list[str]:
    with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as tar:
        return tar.getnames()


def normalize_relative_path(path: str | None, *, field_name: str = "路径") -> str:
    trimmed = (path or "").strip().replace("\\", "/")
    # 绝对路径必须在 strip("/") 之前判：先剥前导斜杠会把 "/etc" 静默改写成仓库内的 "etc"。
    if trimmed.startswith("/") or Path(trimmed).is_absolute():
        raise ValueError(f"{field_name}必须是相对路径")
    normalized = trimmed.strip("/")
    if not normalized or normalized == ".":
        raise ValueError(f"{field_name}不能为空")
    parts = normalized.split("/")
    if ".." in parts or ".git" in parts:
        raise ValueError(f"{field_name}不合法")
    return normalized


def normalize_source_subdir(path: str | None) -> str:
    normalized = (path or "").strip().replace("\\", "/").strip("/")
    if not normalized or normalized == ".":
        raise ValueError("Git 子目录不能为空，必须显式指向仓库内项目目录")
    return normalize_relative_path(normalized, field_name="Git 子目录")


def string_list(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("include_paths 必须是数组")
    if len(value) > MAX_BUNDLE_INCLUDE_PATHS:
        raise ValueError(f"include_paths 超过上限 {MAX_BUNDLE_INCLUDE_PATHS}")
    normalized = [normalize_relative_path(str(item), field_name="include_paths") for item in value]
    if len(normalized) != len(set(normalized)):
        raise ValueError("include_paths 不允许重复")
    return normalized


def resolve_existing_dir(root: Path, relative_path: str, label: str) -> Path:
    target = resolve_inside(root, relative_path, label)
    if not target.exists():
        raise FileNotFoundError(f"{label}不存在: {relative_path}")
    if not target.is_dir():
        raise ValueError(f"{label}必须是目录: {relative_path}")
    return target


def resolve_entry_point(source_root: Path, entry_point: str) -> Path:
    target = resolve_inside(source_root, entry_point, "入口文件")
    if not target.exists():
        raise FileNotFoundError(f"入口文件不存在: {entry_point}")
    if not target.is_file():
        raise ValueError(f"入口文件必须是文件: {entry_point}")
    return target


def resolve_inside(root: Path, relative_path: str, label: str) -> Path:
    target = (root / relative_path).resolve()
    resolved_root = root.resolve()
    if os.path.commonpath([str(resolved_root), str(target)]) != str(resolved_root):
        raise ValueError(f"{label}越界")
    if any(part == ".git" for part in target.relative_to(resolved_root).parts):
        raise ValueError(f"{label}不能包含 .git")
    return target


def iter_unique_source_files(repo_root: Path, roots: list[Path]) -> list[Path]:
    files: dict[str, Path] = {}
    for source_root in roots:
        for path in iter_source_files(source_root):
            relative = path.resolve().relative_to(repo_root).as_posix()
            files[relative] = path
            if len(files) > MAX_BUNDLE_FILE_COUNT:
                raise ValueError(f"source bundle 文件数超过上限: {len(files)} > {MAX_BUNDLE_FILE_COUNT}")
    return [files[name] for name in sorted(files)]


def iter_source_files(source_root: Path) -> list[Path]:
    files: list[Path] = []
    for current, dirnames, filenames in os.walk(source_root):
        dirnames[:] = sorted(name for name in dirnames if name != ".git")
        for filename in sorted(filenames):
            path = Path(current) / filename
            if not path.is_symlink() and path.is_file():
                files.append(path)
                if len(files) > MAX_BUNDLE_FILE_COUNT:
                    raise ValueError(f"source bundle 文件数超过上限: {len(files)} > {MAX_BUNDLE_FILE_COUNT}")
    return files


def add_file(tar: tarfile.TarFile, repo_root: Path, path: Path) -> None:
    arcname = path.resolve().relative_to(repo_root).as_posix()
    info = tar.gettarinfo(str(path), arcname=arcname)
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    with open(path, "rb") as file_obj:
        tar.addfile(info, file_obj)


def _candidate(relative: str, marker_files: list[str]) -> dict[str, object]:
    return {
        "subdir": relative,
        "entry_point": _select_entry_point(marker_files),
        "markers": marker_files,
    }


def _select_entry_point(marker_files: list[str]) -> str:
    for candidate in ENTRYPOINT_CANDIDATES:
        if candidate in marker_files:
            return candidate
    raise ValueError("候选项目缺少可执行入口文件")
