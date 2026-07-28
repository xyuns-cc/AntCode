"""Windows HANDLE-based artifact access with reparse-point rejection."""

from __future__ import annotations

import hashlib
import ntpath
import os
import re
import stat
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from antcode_worker.executor.artifact_io_windows_kernel import (
    ArtifactFileIdentity,
    Kernel32Api,
    WindowsEntryInfo,
    WindowsKernel,
)

READ_CHUNK_SIZE = 64 * 1024
DRIVE_PATTERN = re.compile(r"^[A-Za-z]:$")
RESERVED_DEVICE_NAMES = frozenset({"CON", "PRN", "AUX", "NUL", "CLOCK$"})
RESERVED_DEVICE_PREFIXES = ("COM", "LPT")
RESERVED_DEVICE_SUFFIXES = frozenset("123456789\u00b9\u00b2\u00b3")
INVALID_FILENAME_CHARS = frozenset('<>"|?*')


class WindowsArtifactSecurityError(ValueError):
    """A Windows artifact path escaped or changed across the boundary."""


@dataclass(frozen=True)
class VerifiedArtifactFile:
    identity: ArtifactFileIdentity
    checksum: str


@dataclass(frozen=True)
class VerifiedArtifactRoot:
    path: Path
    identity: ArtifactFileIdentity


def inspect_work_root(work_root: Path, *, kernel: WindowsKernel | None = None) -> VerifiedArtifactRoot:
    api = kernel or Kernel32Api()
    absolute = _absolute_path(work_root)
    with ExitStack() as stack:
        handles = _open_directory_chain(api, absolute, stack)
        info = _validated_info(api, handles[-1], absolute, require_directory=True)
        _verify_same_path(api.final_path(handles[-1]), absolute)
        return VerifiedArtifactRoot(Path(absolute), info.identity)


def inspect_candidate(
    path: Path,
    relative_path: str,
    *,
    kernel: WindowsKernel | None = None,
) -> ArtifactFileIdentity | None:
    root = _candidate_root(path, relative_path)
    handle, info, close = _open_entry(root, relative_path, kernel=kernel)
    try:
        return None if info.is_directory else _regular_identity(info, relative_path)
    finally:
        close()


def hash_regular_file(
    work_root: Path,
    relative_path: str,
    *,
    expected_root: ArtifactFileIdentity,
    expected: ArtifactFileIdentity,
    algorithm: str,
    max_bytes: int,
    kernel: WindowsKernel | None = None,
) -> VerifiedArtifactFile:
    handle, info, close = _open_entry(work_root, relative_path, expected_root=expected_root, kernel=kernel)
    try:
        opened = _regular_identity(info, relative_path)
        _verify_expected(opened, expected, relative_path)
        _, checksum = _read_bounded(handle, max_bytes, algorithm, kernel=kernel)
        _verify_after_read(handle, opened, relative_path, kernel=kernel)
        return VerifiedArtifactFile(opened, checksum)
    finally:
        close()


def read_verified_regular_file(
    work_root: Path,
    relative_path: str,
    *,
    expected_root: ArtifactFileIdentity,
    expected: ArtifactFileIdentity | None,
    expected_checksum: str | None,
    algorithm: str,
    max_bytes: int,
    kernel: WindowsKernel | None = None,
) -> bytes:
    handle, info, close = _open_entry(work_root, relative_path, expected_root=expected_root, kernel=kernel)
    try:
        opened = _regular_identity(info, relative_path)
        if expected is not None:
            _verify_expected(opened, expected, relative_path)
        content, checksum = _read_bounded(handle, max_bytes, algorithm, kernel=kernel)
        _verify_after_read(handle, opened, relative_path, kernel=kernel)
        if expected_checksum is not None and checksum != expected_checksum:
            raise WindowsArtifactSecurityError(f"产物内容在收集后发生变化: {relative_path}")
        return content
    finally:
        close()


def _open_entry(
    work_root: Path,
    relative_path: str,
    *,
    expected_root: ArtifactFileIdentity | None = None,
    kernel: WindowsKernel | None = None,
) -> tuple[int, WindowsEntryInfo, Callable[[], None]]:
    api = kernel or Kernel32Api()
    root = _absolute_path(work_root)
    parts = _relative_parts(relative_path)
    stack = ExitStack()
    try:
        root_handles = _open_directory_chain(api, root, stack)
        root_info = _validated_info(api, root_handles[-1], root, require_directory=True)
        if expected_root is not None:
            _verify_expected(root_info.identity, expected_root, str(root))
        parent = PureWindowsPath(root)
        for part in parts[:-1]:
            parent /= part
            handle = api.open_directory(str(parent))
            stack.callback(api.close, handle)
            _validated_info(api, handle, str(parent), require_directory=True)
        leaf_path = str(parent / parts[-1])
        handle = api.open_entry(leaf_path)
        stack.callback(api.close, handle)
        info = _validated_info(api, handle, relative_path)
        _verify_contained(api.final_path(root_handles[-1]), api.final_path(handle))
        return handle, info, stack.close
    except Exception:
        stack.close()
        raise


def _open_directory_chain(api: WindowsKernel, path: str, stack: ExitStack) -> list[int]:
    pure = PureWindowsPath(path)
    current = PureWindowsPath(f"{pure.drive}\\")
    handles = []
    for part in (None, *pure.parts[1:]):
        if part is not None:
            current /= part
        handle = api.open_directory(str(current))
        stack.callback(api.close, handle)
        _validated_info(api, handle, str(current), require_directory=True)
        handles.append(handle)
    return handles


def _validated_info(
    api: WindowsKernel,
    handle: int,
    display_path: str,
    *,
    require_directory: bool = False,
) -> WindowsEntryInfo:
    info = api.entry_info(handle)
    if info.is_reparse_point:
        raise WindowsArtifactSecurityError(f"产物路径不得包含 reparse point: {display_path}")
    if require_directory and not info.is_directory:
        raise WindowsArtifactSecurityError(f"产物工作目录必须是真实目录: {display_path}")
    return info


def _regular_identity(info: WindowsEntryInfo, relative_path: str) -> ArtifactFileIdentity:
    if info.is_directory or not stat.S_ISREG(info.identity.mode):
        raise WindowsArtifactSecurityError(f"产物必须是常规文件: {relative_path}")
    if info.identity.link_count != 1:
        raise WindowsArtifactSecurityError(f"产物不得是硬链接文件: {relative_path}")
    return info.identity


def _read_bounded(
    handle: int,
    max_bytes: int,
    algorithm: str,
    *,
    kernel: WindowsKernel | None,
) -> tuple[bytes, str]:
    api = kernel or Kernel32Api()
    chunks = []
    hasher = _new_hasher(algorithm)
    total = 0
    while chunk := api.read(handle, READ_CHUNK_SIZE):
        total += len(chunk)
        if total > max_bytes:
            raise WindowsArtifactSecurityError(f"产物读取大小超过已验证上限: {max_bytes}")
        chunks.append(chunk)
        hasher.update(chunk)
    return b"".join(chunks), hasher.hexdigest()


def _verify_after_read(
    handle: int,
    opened: ArtifactFileIdentity,
    relative_path: str,
    *,
    kernel: WindowsKernel | None,
) -> None:
    api = kernel or Kernel32Api()
    if api.entry_info(handle).identity != opened:
        raise WindowsArtifactSecurityError(f"产物在读取过程中发生变化: {relative_path}")


def _absolute_path(path: os.PathLike[str] | str) -> str:
    pure = PureWindowsPath(str(path))
    if not DRIVE_PATTERN.fullmatch(pure.drive) or not pure.root:
        raise WindowsArtifactSecurityError(f"Windows 产物路径必须是本地绝对路径: {path}")
    if any(_invalid_relative_part(part) for part in pure.parts[1:]):
        raise WindowsArtifactSecurityError(f"Windows 产物路径无效、包含 ADS 或设备名: {path}")
    return str(pure)


def _relative_parts(relative_path: str) -> tuple[str, ...]:
    pure = PureWindowsPath(relative_path)
    if pure.drive or pure.root or not pure.parts or ".." in pure.parts:
        raise WindowsArtifactSecurityError(f"产物路径越过工作目录: {relative_path}")
    if any(_invalid_relative_part(part) for part in pure.parts):
        raise WindowsArtifactSecurityError(f"Windows 产物路径无效或包含 ADS: {relative_path}")
    return pure.parts


def _invalid_relative_part(part: str) -> bool:
    invalid_character = any(character in INVALID_FILENAME_CHARS or ord(character) < 32 for character in part)
    if part in {"", "."} or ":" in part or part.endswith((" ", ".")) or invalid_character:
        return True
    device_name = part.split(".", maxsplit=1)[0].upper()
    if device_name in RESERVED_DEVICE_NAMES:
        return True
    prefix, suffix = device_name[:3], device_name[3:]
    return prefix in RESERVED_DEVICE_PREFIXES and suffix in RESERVED_DEVICE_SUFFIXES


def _candidate_root(path: Path, relative_path: str) -> Path:
    root = PureWindowsPath(str(path))
    for _ in _relative_parts(relative_path):
        root = root.parent
    return Path(str(root))


def _verify_expected(actual: ArtifactFileIdentity, expected: ArtifactFileIdentity, path: str) -> None:
    if actual != expected:
        raise WindowsArtifactSecurityError(f"产物或工作目录在检查后发生变化: {path}")


def _verify_contained(root: str, child: str) -> None:
    normalized_root = ntpath.normcase(ntpath.normpath(root))
    normalized_child = ntpath.normcase(ntpath.normpath(child))
    try:
        common = ntpath.commonpath((normalized_root, normalized_child))
    except ValueError:
        common = ""
    if common != normalized_root:
        raise WindowsArtifactSecurityError(f"产物路径越过工作目录: {child}")


def _verify_same_path(actual: str, expected: str) -> None:
    if ntpath.normcase(ntpath.normpath(actual)) != ntpath.normcase(ntpath.normpath(expected)):
        raise WindowsArtifactSecurityError(f"产物工作目录解析后发生变化: {expected}")


def _new_hasher(algorithm: str):
    if algorithm == "sha256":
        return hashlib.sha256()
    if algorithm == "sha1":
        return hashlib.sha1(usedforsecurity=False)
    if algorithm == "md5":
        return hashlib.md5(usedforsecurity=False)
    raise ValueError(f"不支持的产物校验算法: {algorithm}")


__all__ = [
    "ArtifactFileIdentity",
    "Kernel32Api",
    "VerifiedArtifactFile",
    "VerifiedArtifactRoot",
    "WindowsArtifactSecurityError",
    "WindowsEntryInfo",
    "hash_regular_file",
    "inspect_candidate",
    "inspect_work_root",
    "read_verified_regular_file",
]
