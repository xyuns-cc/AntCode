"""Minimal, allow-listed filesystem view for bubblewrap payloads."""

from __future__ import annotations

import importlib.util
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from antcode_worker.executor.sandbox_executables import executable_mount_roots
from antcode_worker.executor.sandbox_scope import validate_bundle_root_scope, validate_workspace_scope

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
_BYTES_PER_MIB = 1024 * 1024


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
    # source bundle 解包根目录；include_paths 共享目录是 work_dir 的兄弟节点，
    # 只 bind work_dir 会让它们在沙箱里彻底不存在。
    bundle_root: Path | None = None
    # 每个内存盘挂载点（/tmp、/dev/shm）的字节上限，单位 MB。见 _private_namespace_args。
    # 无默认值：默认值只能是"不下 --size"，而那正是内核按宿主内存的一半建盘的入口。
    tmpfs_size_mb: int = field(kw_only=True)


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
    bundle_root = _optional_bundle_root(request, work_dir=work_dir, data_root=data_root)
    executable = _resolve_executable(request.payload_executable)
    runtime_executable = _optional_executable(request.runtime_executable)

    args = _private_namespace_args(request.tmpfs_size_mb)
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
    if bundle_root is not None:
        # 顺序有意义：先把整个 bundle 挂成只读，再用可写 bind 覆盖项目子目录，
        # 于是共享依赖只读、任务产物仍只能写进 work_dir。
        _append_read_only(args, bundle_root, mounted=mounted)
    _append_writable(args, work_dir)
    return tuple(args)


def _optional_bundle_root(
    request: SandboxFilesystemRequest,
    *,
    work_dir: Path,
    data_root: Path,
) -> Path | None:
    if request.bundle_root is None:
        return None
    bundle_root = _required_directory(request.bundle_root, label="source bundle 根目录")
    validate_bundle_root_scope(bundle_root, work_dir=work_dir, data_root=data_root, run_id=request.run_id)
    return bundle_root


def _private_namespace_args(tmpfs_size_mb: int) -> list[str]:
    """私有 /dev、/proc 与两个内存盘（/dev/shm、/tmp）。

    两个 tmpfs 必须显式定尺寸。不给 ``--size`` 时内核按**宿主内存的一半**建 tmpfs：
    真机实测（宿主 32GB）任务里 ``statvfs`` 两处各报 16046MB，而整个 Worker 容器的
    ``memory.max`` 只有 8192MB——单个任务的一个内存盘就是容器额度的 2 倍。这是典型的
    "值从宿主算出来"：尺寸的来源与它要约束的对象不在同一个坐标系。

    tmpfs 页计入容器 memory cgroup 却不进任何进程的 RSS（``write()`` 写下去的页没有
    映射），所以进程树 RSS 监控看不见它们，``--size`` 是这条路径**唯一**的每任务边界。

    尺寸取本任务自己的内存限额（``effective_memory_limit_mb``）——不是新发明的比例，
    就是同一个已经用来收 RLIMIT_DATA 与 RSS 的数：任务对容器内存的占用，无论走哪条
    通道，都不该超过它自己那一份。

    重复计账（必须知情）：任务池已被 RSS 100% 分光（``task_memory_limit_mb`` =
    任务池 / 并发），所以任何非零的 tmpfs 预算按定义都是超配。最坏情况单任务
    = RSS + /tmp + /dev/shm = **3.5 倍**限额，容器级 mem_limit 是硬顶。是 3.5 不是 3：
    RSS 那一份的上界不是 1 倍而是 ~1.5 倍——它由轮询监控兑现，而
    ``monitor_interval._OVERSHOOT_BUDGET_RATIO`` 显式允许 50% 超支，按 1 倍算就是把那条
    已写明的超支预算漏掉了。取这个代价是因为另一条路（把限额砍到 1/3 给 tmpfs 腾地方）
    会让每个任务的可用内存三等分，代价远大于收益。改动前后的单任务最坏值
    （限额 1433MB、宿主 32GB）：2×16046 + 1.5×1433 = 34242MB → 3.5 × 限额。
    """
    return [
        "--dev",
        "/dev",
        "--dir",
        _PRIVATE_SHARED_MEMORY,
        *_tmpfs_size_args(tmpfs_size_mb),
        "--tmpfs",
        _PRIVATE_SHARED_MEMORY,
        "--chmod",
        _PRIVATE_SHARED_MEMORY_MODE,
        _PRIVATE_SHARED_MEMORY,
        "--proc",
        "/proc",
        *_tmpfs_size_args(tmpfs_size_mb),
        "--tmpfs",
        "/tmp",
        "--dir",
        _PRIVATE_HOME,
    ]


def _tmpfs_size_args(tmpfs_size_mb: int) -> tuple[str, ...]:
    """``--size`` 只作用于紧随其后的那一个 ``--tmpfs``（bwrap 语义），故每个挂载点各下一次。

    ``<= 0`` 不是"不限制"而是**接线断了**：``init_worker_config`` 恒把 0 换成自适应或
    默认限额（下界 256MB），``engine/config_update`` 走同一条区间校验，运行期没有合法
    路径能送来 0。旧实现"0 就不下 --size"，于是断掉的接线会安静退回**宿主内存的一半**
    ——防护不是被关掉，是被换成了本模块正要消灭的那个值。
    """
    if tmpfs_size_mb <= 0:
        raise RuntimeError(
            f"沙箱内存盘尺寸不可知({tmpfs_size_mb})：0/负数不表示不限制，而是任务级内存限额没接上。"
            "此时不下 --size 会让内核按宿主内存的一半建 /tmp 与 /dev/shm，单个任务的一个内存盘"
            "就能超出整个容器的额度。请检查资源限额接线"
            "(config.init_worker_config → ExecutorConfig.default_memory_limit_mb → effective_memory_limit_mb)。"
        )
    return ("--size", str(tmpfs_size_mb * _BYTES_PER_MIB))


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
