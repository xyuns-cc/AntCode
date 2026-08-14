"""Minimal, allow-listed filesystem view for bubblewrap payloads."""

from __future__ import annotations

import importlib.util
import shutil
from dataclasses import dataclass
from pathlib import Path

from antcode_worker.executor.sandbox_executables import executable_mount_roots
from antcode_worker.executor.sandbox_scope import validate_workspace_scope

_SYSTEM_ROOTS = (Path("/usr"), Path("/bin"), Path("/sbin"), Path("/lib"), Path("/lib64"))
_SAFE_ETC_DIRS = (Path("/etc/ssl/certs"), Path("/etc/ca-certificates"), Path("/etc/fonts"))
_SAFE_ETC_FILES = (
    Path("/etc/ld.so.cache"),
    Path("/etc/ld.so.conf"),
    Path("/etc/nsswitch.conf"),
    Path("/etc/hosts"),
    Path("/etc/resolv.conf"),
    Path("/etc/localtime"),
    Path("/etc/timezone"),
    Path("/etc/passwd"),
    Path("/etc/group"),
)
_BROWSER_ROOT = Path("/opt/ms-playwright")
_BROWSER_PLUGINS = frozenset({"render", "rule", "spider"})
_TRUSTED_INTERNAL_PACKAGES = ("antcode_worker", "antcode_scrapy", "antcode_core", "antcode_contracts")
_INTERNAL_PACKAGE_PLUGINS = frozenset({"render", "rule", "spider"})
_PRIVATE_HOME = "/tmp/antcode-home"
_PRIVATE_SHARED_MEMORY = "/dev/shm"
_PRIVATE_SHARED_MEMORY_MODE = "1777"
_FORBIDDEN_RUNTIME_ROOTS = frozenset({Path("/").resolve(), Path("/tmp").resolve()})


@dataclass(frozen=True)
class SandboxFilesystemRequest:
    """All host paths a single payload is permitted to see."""

    work_dir: Path
    payload_executable: str
    data_root: Path
    runtimes_root: Path
    plugin_name: str | None = None
    run_id: str | None = None
    runtime_dir: Path | None = None
    runtime_executable: Path | None = None


def sandbox_filesystem_args(request: SandboxFilesystemRequest) -> tuple[str, ...]:
    """Build a fail-closed bwrap filesystem from an explicit allow list."""
    data_root = _required_directory(request.data_root, label="Worker 数据根目录")
    runtimes_root = _required_directory(request.runtimes_root, label="Worker runtime 根目录")
    work_dir = _required_directory(request.work_dir, label="任务工作目录")
    validate_workspace_scope(
        work_dir,
        data_root=data_root,
        runtimes_root=runtimes_root,
        plugin_name=request.plugin_name,
        run_id=request.run_id,
    )
    runtime_dir = _optional_runtime_directory(
        request.runtime_dir,
        work_dir=work_dir,
        data_root=data_root,
        runtimes_root=runtimes_root,
    )
    executable = _resolve_executable(request.payload_executable)
    runtime_executable = _optional_executable(request.runtime_executable)

    args = _private_namespace_args()
    mounted: set[tuple[Path, Path]] = set()
    for path in _required_system_roots():
        _append_read_only(args, path, mounted=mounted)
    for path in (*_existing(_SAFE_ETC_DIRS, directory=True), *_existing(_SAFE_ETC_FILES, directory=False)):
        _append_read_only(args, path, mounted=mounted)
    if runtime_dir is not None:
        _append_read_only(args, runtime_dir, mounted=mounted)
    for path in executable_mount_roots(
        executable,
        runtime_executable,
        work_dir=work_dir,
        runtime_dir=runtime_dir,
        data_root=data_root,
    ):
        _append_read_only(args, path, mounted=mounted)
    for path in _trusted_application_paths(request.plugin_name):
        _append_read_only(args, path, mounted=mounted)
    _append_writable(args, work_dir)
    return tuple(args)


def _private_namespace_args() -> list[str]:
    return [
        "--dev",
        "/dev",
        "--dir",
        _PRIVATE_SHARED_MEMORY,
        "--tmpfs",
        _PRIVATE_SHARED_MEMORY,
        "--chmod",
        _PRIVATE_SHARED_MEMORY_MODE,
        _PRIVATE_SHARED_MEMORY,
        "--proc",
        "/proc",
        "--tmpfs",
        "/tmp",
        "--dir",
        _PRIVATE_HOME,
    ]


def _required_system_roots() -> tuple[Path, ...]:
    roots = _existing(_SYSTEM_ROOTS, directory=True)
    if not any(path == Path("/usr") for path in roots):
        raise RuntimeError("bwrap 最小文件系统要求宿主存在 /usr")
    return roots


def _required_directory(path: Path, *, label: str) -> Path:
    if not path.is_absolute():
        raise RuntimeError(f"{label}必须是绝对路径")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError(f"{label}不可用: {path}") from exc
    if not resolved.is_dir():
        raise RuntimeError(f"{label}不是目录: {resolved}")
    return resolved


def _optional_runtime_directory(
    path: Path | None,
    *,
    work_dir: Path,
    data_root: Path,
    runtimes_root: Path,
) -> Path | None:
    if path is None:
        return None
    runtime = _required_directory(path, label="任务 runtime")
    broad_roots = {*_FORBIDDEN_RUNTIME_ROOTS, data_root, runtimes_root}
    invalid_scope = runtime in broad_roots or runtime in work_dir.parents
    if invalid_scope or runtime.parent != runtimes_root:
        raise RuntimeError(f"任务 runtime 范围过宽，拒绝挂载: {runtime}")
    return runtime


def _resolve_executable(value: str) -> tuple[Path, Path]:
    candidate = Path(value)
    if not candidate.is_absolute():
        resolved_name = shutil.which(value)
        if resolved_name is None:
            raise RuntimeError(f"任务可执行文件不存在: {value}")
        candidate = Path(resolved_name)
    return candidate, _required_file(candidate, label="任务可执行文件")


def _optional_executable(path: Path | None) -> tuple[Path, Path] | None:
    if path is None:
        return None
    return path, _required_file(path, label="runtime 可执行文件")


def _required_file(path: Path, *, label: str) -> Path:
    if not path.is_absolute():
        raise RuntimeError(f"{label}必须是绝对路径")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError(f"{label}不可用: {path}") from exc
    if not resolved.is_file():
        raise RuntimeError(f"{label}不是普通文件: {resolved}")
    return resolved


def _trusted_application_paths(plugin_name: str | None) -> tuple[Path, ...]:
    paths: list[Path] = []
    if plugin_name in _INTERNAL_PACKAGE_PLUGINS:
        paths.extend(_package_path(name) for name in _TRUSTED_INTERNAL_PACKAGES)
    if plugin_name in _BROWSER_PLUGINS and _BROWSER_ROOT.is_dir():
        paths.append(_BROWSER_ROOT.resolve(strict=True))
    return tuple(dict.fromkeys(paths))


def _package_path(name: str) -> Path:
    spec = importlib.util.find_spec(name)
    if spec is None or spec.origin is None:
        raise RuntimeError(f"沙箱所需内建包不可用: {name}")
    return _required_directory(Path(spec.origin).parent, label=f"内建包 {name}")


def _existing(paths: tuple[Path, ...], *, directory: bool) -> tuple[Path, ...]:
    existing: list[Path] = []
    for path in paths:
        try:
            matches = path.is_dir() if directory else path.is_file()
        except OSError:
            matches = False
        if matches:
            existing.append(path)
    return tuple(existing)


def _append_read_only(args: list[str], path: Path, *, mounted: set[tuple[Path, Path]]) -> None:
    source = path.resolve(strict=True)
    mount = (source, path)
    if mount in mounted:
        return
    _append_parent_dirs(args, path)
    args.extend(("--ro-bind", str(source), str(path)))
    mounted.add(mount)


def _append_writable(args: list[str], path: Path) -> None:
    _append_parent_dirs(args, path)
    args.extend(("--bind", str(path), str(path)))


def _append_parent_dirs(args: list[str], path: Path) -> None:
    parents = tuple(reversed(path.parents[:-1]))
    for parent in parents:
        args.extend(("--dir", str(parent)))


def private_home() -> str:
    """Return the writable HOME created inside every sandbox namespace."""
    return _PRIVATE_HOME


__all__ = ["SandboxFilesystemRequest", "private_home", "sandbox_filesystem_args"]
