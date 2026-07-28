"""POSIX fail-closed filesystem access for task artifacts."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

READ_CHUNK_SIZE = 64 * 1024


class ArtifactSecurityError(ValueError):
    """An artifact candidate escaped or changed across the trust boundary."""


@dataclass(frozen=True)
class ArtifactFileIdentity:
    device: int
    inode: int
    mode: int
    link_count: int
    size: int
    modified_ns: int
    changed_ns: int


@dataclass(frozen=True)
class VerifiedArtifactFile:
    identity: ArtifactFileIdentity
    checksum: str


@dataclass(frozen=True)
class VerifiedArtifactRoot:
    path: Path
    identity: ArtifactFileIdentity


def inspect_work_root(work_root: Path) -> VerifiedArtifactRoot:
    """Open every absolute path component without following symlinks."""
    absolute_root = Path(os.path.abspath(work_root))
    root_fd = _open_root_path(absolute_root)
    try:
        identity = _identity(os.fstat(root_fd))
        if not stat.S_ISDIR(identity.mode):
            raise ArtifactSecurityError(f"产物工作目录必须是真实目录: {absolute_root}")
        return VerifiedArtifactRoot(path=absolute_root, identity=identity)
    finally:
        os.close(root_fd)


def validate_artifact_pattern(pattern: str) -> None:
    """Reject absolute and parent-traversing glob patterns before expansion."""
    if not pattern or "\x00" in pattern:
        raise ArtifactSecurityError("产物匹配模式不能为空或包含 NUL")
    posix = PurePosixPath(pattern)
    windows = PureWindowsPath(pattern)
    if posix.is_absolute() or windows.is_absolute():
        raise ArtifactSecurityError(f"产物匹配模式不得是绝对路径: {pattern}")
    if ".." in posix.parts or ".." in windows.parts:
        raise ArtifactSecurityError(f"产物匹配模式不得越过工作目录: {pattern}")


def inspect_candidate(path: Path, relative_path: str) -> ArtifactFileIdentity | None:
    """Inspect a glob result without following its final path component."""
    try:
        candidate_stat = path.lstat()
    except OSError as exc:
        raise ArtifactSecurityError(f"产物候选在检查前已变化: {relative_path}") from exc
    if stat.S_ISDIR(candidate_stat.st_mode):
        return None
    identity = _identity(candidate_stat)
    _validate_regular_identity(identity, relative_path)
    return identity


def hash_regular_file(
    work_root: Path,
    relative_path: str,
    *,
    expected_root: ArtifactFileIdentity,
    expected: ArtifactFileIdentity,
    algorithm: str,
    max_bytes: int,
) -> VerifiedArtifactFile:
    """Open a candidate beneath work_root and hash the verified descriptor."""
    fd, opened = _open_regular_file(
        work_root,
        relative_path,
        expected_root=expected_root,
        expected=expected,
    )
    try:
        hasher = _new_hasher(algorithm)
        total = 0
        for chunk in iter(lambda: os.read(fd, READ_CHUNK_SIZE), b""):
            total += len(chunk)
            if total > max_bytes:
                raise ArtifactSecurityError(f"产物读取大小超过已验证上限: {max_bytes}")
            hasher.update(chunk)
        _verify_after_read(fd, opened, relative_path)
        return VerifiedArtifactFile(identity=opened, checksum=hasher.hexdigest())
    finally:
        os.close(fd)


def read_verified_regular_file(
    work_root: Path,
    relative_path: str,
    *,
    expected_root: ArtifactFileIdentity,
    expected: ArtifactFileIdentity | None,
    expected_checksum: str | None,
    algorithm: str,
    max_bytes: int,
) -> bytes:
    """Read one bounded regular file and revalidate identity and content."""
    fd, opened = _open_regular_file(
        work_root,
        relative_path,
        expected_root=expected_root,
        expected=expected,
    )
    try:
        content, checksum = _read_bounded(fd, max_bytes, algorithm)
        _verify_after_read(fd, opened, relative_path)
        if expected_checksum is not None and checksum != expected_checksum:
            raise ArtifactSecurityError(f"产物内容在收集后发生变化: {relative_path}")
        return content
    finally:
        os.close(fd)


def _open_regular_file(
    work_root: Path,
    relative_path: str,
    *,
    expected_root: ArtifactFileIdentity,
    expected: ArtifactFileIdentity | None,
) -> tuple[int, ArtifactFileIdentity]:
    parts = _relative_parts(relative_path)
    directory_fd = _open_root(work_root, expected_root)
    try:
        directory_fd = _open_parent_directories(directory_fd, parts[:-1], relative_path)
        file_fd = _open_leaf(directory_fd, parts[-1], relative_path)
    finally:
        os.close(directory_fd)
    try:
        opened = _identity(os.fstat(file_fd))
        _validate_regular_identity(opened, relative_path)
        if expected is not None and opened != expected:
            raise ArtifactSecurityError(f"产物在匹配后发生变化: {relative_path}")
    except Exception:
        os.close(file_fd)
        raise
    return file_fd, opened


def _relative_parts(relative_path: str) -> tuple[str, ...]:
    path = PurePosixPath(relative_path)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ArtifactSecurityError(f"产物路径越过工作目录: {relative_path}")
    if any(part in {"", "."} for part in path.parts):
        raise ArtifactSecurityError(f"产物路径无效: {relative_path}")
    return path.parts


def _open_root(work_root: Path, expected: ArtifactFileIdentity) -> int:
    root = Path(os.path.abspath(work_root))
    root_fd = _open_root_path(root)
    try:
        opened = _identity(os.fstat(root_fd))
        if opened != expected:
            raise ArtifactSecurityError(f"产物工作目录在收集后发生变化: {root}")
        return root_fd
    except Exception:
        os.close(root_fd)
        raise


def _open_root_path(root: Path) -> int:
    _ensure_supported_platform()
    flags = os.O_RDONLY | _required_flag("O_DIRECTORY") | _required_flag("O_NOFOLLOW")
    flags |= getattr(os, "O_CLOEXEC", 0)
    current_fd = os.open("/", flags)
    try:
        for part in root.parts[1:]:
            next_fd = os.open(part, flags, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except OSError as exc:
        os.close(current_fd)
        raise ArtifactSecurityError(f"产物工作目录含符号链接或不可访问: {root}") from exc


def _open_parent_directories(root_fd: int, parts: tuple[str, ...], relative_path: str) -> int:
    current_fd = root_fd
    opened_fds = [root_fd]
    flags = os.O_RDONLY | _required_flag("O_DIRECTORY") | _required_flag("O_NOFOLLOW")
    flags |= getattr(os, "O_CLOEXEC", 0)
    try:
        for part in parts:
            next_fd = os.open(part, flags, dir_fd=current_fd)
            current_fd = next_fd
            opened_fds.append(next_fd)
    except OSError as exc:
        for opened_fd in opened_fds[1:]:
            os.close(opened_fd)
        raise ArtifactSecurityError(f"产物父目录不安全或已变化: {relative_path}") from exc
    for opened_fd in opened_fds[:-1]:
        os.close(opened_fd)
    return current_fd


def _open_leaf(directory_fd: int, name: str, relative_path: str) -> int:
    flags = os.O_RDONLY | _required_flag("O_NOFOLLOW") | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    try:
        return os.open(name, flags, dir_fd=directory_fd)
    except OSError as exc:
        raise ArtifactSecurityError(f"产物不是可安全打开的常规文件: {relative_path}") from exc


def _read_bounded(fd: int, max_bytes: int, algorithm: str) -> tuple[bytes, str]:
    chunks: list[bytes] = []
    hasher = _new_hasher(algorithm)
    total = 0
    while chunk := os.read(fd, READ_CHUNK_SIZE):
        total += len(chunk)
        if total > max_bytes:
            raise ArtifactSecurityError(f"产物读取大小超过已验证上限: {max_bytes}")
        chunks.append(chunk)
        hasher.update(chunk)
    return b"".join(chunks), hasher.hexdigest()


def _verify_after_read(fd: int, opened: ArtifactFileIdentity, relative_path: str) -> None:
    if _identity(os.fstat(fd)) != opened:
        raise ArtifactSecurityError(f"产物在读取过程中发生变化: {relative_path}")


def _identity(result: os.stat_result) -> ArtifactFileIdentity:
    return ArtifactFileIdentity(
        device=result.st_dev,
        inode=result.st_ino,
        mode=result.st_mode,
        link_count=result.st_nlink,
        size=result.st_size,
        modified_ns=result.st_mtime_ns,
        changed_ns=result.st_ctime_ns,
    )


def _validate_regular_identity(identity: ArtifactFileIdentity, relative_path: str) -> None:
    if not stat.S_ISREG(identity.mode):
        raise ArtifactSecurityError(f"产物必须是常规文件: {relative_path}")
    if identity.link_count != 1:
        raise ArtifactSecurityError(f"产物不得是硬链接文件: {relative_path}")


def _new_hasher(algorithm: str):
    if algorithm == "sha256":
        return hashlib.sha256()
    if algorithm == "sha1":
        return hashlib.sha1(usedforsecurity=False)
    if algorithm == "md5":
        return hashlib.md5(usedforsecurity=False)
    raise ValueError(f"不支持的产物校验算法: {algorithm}")


def _required_flag(name: str) -> int:
    value = getattr(os, name, None)
    if value is None:
        raise RuntimeError(f"当前平台缺少安全产物读取所需的 {name}")
    return value


def _ensure_supported_platform() -> None:
    if os.name != "posix" or os.open not in os.supports_dir_fd:
        raise ArtifactSecurityError("安全产物收集仅支持具备 openat/O_NOFOLLOW 的 POSIX Worker")


__all__ = [
    "ArtifactFileIdentity",
    "ArtifactSecurityError",
    "VerifiedArtifactFile",
    "VerifiedArtifactRoot",
    "hash_regular_file",
    "inspect_work_root",
    "inspect_candidate",
    "read_verified_regular_file",
    "validate_artifact_pattern",
]
