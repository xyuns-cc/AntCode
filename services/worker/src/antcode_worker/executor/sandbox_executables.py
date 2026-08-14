"""Validate executable paths before exposing their installation roots."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_SYSTEM_ROOTS = (Path("/usr"), Path("/bin"), Path("/sbin"), Path("/lib"), Path("/lib64"))
_UV_PYTHON_INSTALL_ENV = "UV_PYTHON_INSTALL_DIR"
_MISE_DATA_ENV = "MISE_DATA_DIR"
_MISE_LANGUAGES = frozenset({"go", "java", "node", "python"})
_SYMLINK_HOP_LIMIT = 8


def executable_mount_roots(
    executable: tuple[Path, Path],
    runtime_executable: tuple[Path, Path] | None,
    *,
    work_dir: Path,
    runtime_dir: Path | None,
    data_root: Path,
) -> tuple[Path, ...]:
    """Return narrowly trusted roots needed by payload executables."""
    _require_workspace_symlink_containment(executable, work_dir)
    visible = (work_dir, runtime_dir)
    roots: list[Path] = []
    for candidate in (*executable, *(runtime_executable or ())):
        for hop in _symlink_hops(candidate):
            _reject_cross_tenant_executable(hop, visible=visible, data_root=data_root)
            if _inside_any(hop, visible) or _inside_any(hop, _SYSTEM_ROOTS):
                continue
            install_root = _executable_install_root(hop)
            _require_trusted_install_root(install_root)
            roots.append(install_root)
    return tuple(dict.fromkeys(roots))


def _symlink_hops(candidate: Path) -> tuple[Path, ...]:
    """符号链接每一跳的字面路径（含起点）。

    只挂"完全解析后"的安装根是不够的：宿主上 uv 装的解释器是两跳——
    venv/bin/python -> <collection>/cpython-3.12-<plat>/bin/python3.12（次版本别名，
    本身是指向目录的软链）-> <collection>/cpython-3.12.13-<plat>/...。namespace 里
    只挂了 3.12.13 那个目录时，别名目录不存在，venv 的软链在沙箱内直接悬空，
    每个任务都以 `prlimit: failed to execute ...: No such file or directory`
    （退出码 127）失败——物理机部署路径 100% 复现，容器画像因为镜像里
    `uv python install <完整补丁版本>` 只有一跳而侥幸躲过。

    每一跳仍逐个走 `_reject_cross_tenant_executable` + `_require_trusted_install_root`，
    允许列表照旧 fail-closed，只是不再漏掉链路中间那个同样可信的安装根。
    """
    hops = [candidate]
    current = candidate
    for _ in range(_SYMLINK_HOP_LIMIT):
        if not current.is_symlink():
            break
        target = Path(os.readlink(current))
        current = target if target.is_absolute() else current.parent / target
        hops.append(current)
    return tuple(hops)


def _require_workspace_symlink_containment(executable: tuple[Path, Path], work_dir: Path) -> None:
    requested, resolved = executable
    if _inside_any(requested, (work_dir,)) and not _inside_any(resolved, (work_dir,)):
        raise RuntimeError("任务工作区可执行文件的符号链接越出当前工作区")


def _reject_cross_tenant_executable(
    candidate: Path,
    *,
    visible: tuple[Path, Path | None],
    data_root: Path,
) -> None:
    if _inside_any(candidate, (data_root,)) and not _inside_any(candidate, visible):
        raise RuntimeError(f"任务可执行文件指向未授权 Worker 数据: {candidate}")


def _executable_install_root(candidate: Path) -> Path:
    if candidate.parent.name != "bin":
        raise RuntimeError(f"任务可执行文件不在允许的 runtime bin 目录: {candidate}")
    install_root = candidate.parent.parent
    if install_root in {Path("/"), Path.home().resolve()}:
        raise RuntimeError(f"任务可执行文件安装根范围过宽: {install_root}")
    return install_root


def _require_trusted_install_root(install_root: Path) -> None:
    if install_root in _worker_install_roots():
        return
    if install_root.parent == _uv_python_collection() or _inside_mise_collection(install_root):
        return
    raise RuntimeError(f"任务可执行文件安装根未列入 Worker 可信范围: {install_root}")


def _worker_install_roots() -> frozenset[Path]:
    executable = Path(sys.executable)
    resolved = executable.resolve(strict=True)
    return frozenset({_executable_install_root(executable), _executable_install_root(resolved)})


def _uv_python_collection() -> Path:
    configured = os.getenv(_UV_PYTHON_INSTALL_ENV, "").strip()
    collection = Path(configured) if configured else Path.home() / ".local" / "share" / "uv" / "python"
    if not collection.is_absolute():
        raise RuntimeError(f"{_UV_PYTHON_INSTALL_ENV} 必须是绝对路径")
    resolved = collection.resolve(strict=False)
    if resolved in {Path("/"), Path("/tmp").resolve(), Path.home().resolve()}:
        raise RuntimeError(f"UV Python 安装集合根范围过宽: {resolved}")
    return resolved


def _inside_mise_collection(install_root: Path) -> bool:
    configured = os.getenv(_MISE_DATA_ENV, "").strip()
    data_root = Path(configured) if configured else Path.home() / ".local" / "share" / "mise"
    collection = _safe_collection_root(data_root / "installs", label="mise 安装集合根")
    language_root = install_root.parent
    return language_root.parent == collection and language_root.name in _MISE_LANGUAGES


def _safe_collection_root(path: Path, *, label: str) -> Path:
    if not path.is_absolute():
        raise RuntimeError(f"{label}必须是绝对路径")
    resolved = path.resolve(strict=False)
    if resolved in {Path("/"), Path("/tmp").resolve(), Path.home().resolve()}:
        raise RuntimeError(f"{label}范围过宽: {resolved}")
    return resolved


def _inside_any(path: Path, roots: tuple[Path | None, ...]) -> bool:
    for root in roots:
        if root is None:
            continue
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


__all__ = ["executable_mount_roots"]
