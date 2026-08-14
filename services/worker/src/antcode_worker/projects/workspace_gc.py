"""Startup cleanup for source workspaces left by interrupted Worker runs."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

WORKSPACE_STALE_TTL_SECONDS = 24 * 60 * 60


def cleanup_stale_workspaces(
    root: Path,
    *,
    now: float | None = None,
    ttl_seconds: int = WORKSPACE_STALE_TTL_SECONDS,
) -> int:
    if ttl_seconds <= 0:
        raise ValueError("workspace stale TTL 必须大于 0")
    cutoff = (time.time() if now is None else now) - ttl_seconds
    cleaned = 0
    for entry in root.iterdir():
        try:
            stat = entry.lstat()
        except FileNotFoundError:
            continue
        if stat.st_mtime >= cutoff:
            continue
        _remove_entry(entry)
        cleaned += 1
    return cleaned


def remove_run_workspace(root: Path, run_id: str) -> None:
    run_dir = root / run_id
    try:
        run_dir.lstat()
    except FileNotFoundError:
        return
    _remove_entry(run_dir)


def _remove_entry(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        path.unlink()
    else:
        shutil.rmtree(path)
    _fsync_directory(path.parent)


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = ["cleanup_stale_workspaces", "remove_run_workspace"]
