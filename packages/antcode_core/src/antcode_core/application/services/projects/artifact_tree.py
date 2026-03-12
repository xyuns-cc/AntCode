"""托管产物目录复制与打包辅助。"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

from fastapi import HTTPException, status


_GIT_METADATA_NAME = ".git"


def copy_materialized_tree(source_dir: Path, target_dir: Path) -> None:
    if source_dir.is_symlink():
        _raise_symlink_error(source_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    for entry in source_dir.iterdir():
        if entry.name == _GIT_METADATA_NAME:
            continue
        destination = target_dir / entry.name
        if entry.is_symlink():
            _raise_symlink_error(entry)
        if entry.is_dir():
            copy_materialized_tree(entry, destination)
            continue
        shutil.copy2(entry, destination)


def zip_materialized_tree(source_dir: Path, archive_path: Path) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file_path in _iter_materialized_files(source_dir):
            archive.write(file_path, file_path.relative_to(source_dir))


def _iter_materialized_files(source_dir: Path):
    for entry in source_dir.iterdir():
        if entry.name == _GIT_METADATA_NAME:
            continue
        if entry.is_symlink():
            _raise_symlink_error(entry)
        if entry.is_dir():
            yield from _iter_materialized_files(entry)
            continue
        yield entry


def _raise_symlink_error(path: Path) -> None:
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Git 仓库包含不支持的符号链接: {path.name}",
    )
