"""Source bundle path validation and archive helpers."""

from __future__ import annotations

import gzip
import io
import os
import tarfile
from pathlib import Path

ENTRYPOINT_CANDIDATES = ("main.py", "spider.py", "crawler.py", "app.py")
PROJECT_MARKERS = ENTRYPOINT_CANDIDATES + ("pyproject.toml", "requirements.txt")


def scan_repository_candidates(repo_dir: Path) -> list[dict[str, object]]:
    root = repo_dir.resolve()
    candidates: list[dict[str, object]] = []
    for current, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name != ".git")
        relative = Path(current).resolve().relative_to(root).as_posix()
        if relative == ".":
            continue
        marker_files = sorted(set(filenames).intersection(PROJECT_MARKERS))
        if marker_files:
            candidates.append(_candidate(relative, marker_files))
    return candidates


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


def list_tar_names(content: bytes) -> list[str]:
    with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as tar:
        return tar.getnames()


def normalize_relative_path(path: str | None, *, field_name: str = "路径") -> str:
    normalized = (path or "").strip().replace("\\", "/").strip("/")
    if not normalized or normalized == ".":
        raise ValueError(f"{field_name}不能为空")
    parts = normalized.split("/")
    if ".." in parts or ".git" in parts:
        raise ValueError(f"{field_name}不合法")
    if Path(normalized).is_absolute():
        raise ValueError(f"{field_name}必须是相对路径")
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
    return [normalize_relative_path(str(item), field_name="include_paths") for item in value]


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
    return [files[name] for name in sorted(files)]


def iter_source_files(source_root: Path) -> list[Path]:
    files: list[Path] = []
    for current, dirnames, filenames in os.walk(source_root):
        dirnames[:] = sorted(name for name in dirnames if name != ".git")
        for filename in sorted(filenames):
            path = Path(current) / filename
            if not path.is_symlink() and path.is_file():
                files.append(path)
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
    return marker_files[0]
