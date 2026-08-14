"""
子进程沙箱限制

为 ``ProcessExecutor`` 构造 ``preexec_fn``：独立进程组 + POSIX rlimit。
从 ``process.py`` 拆出，让后者只保留子进程生命周期编排。

B7/B8: 这里的每一步失败都必须让子进程"根本起不来"——沙箱失效必须表现为
启动失败，绝不能是静默放行。
"""

import os
import sys
from collections.abc import Callable
from dataclasses import dataclass

try:
    import resource

    HAS_RESOURCE = True
except ImportError:
    resource = None  # type: ignore[assignment]
    HAS_RESOURCE = False

_BWRAP_EXECUTABLE = "bwrap"
_BYTES_PER_MIB = 1024 * 1024

# B8: Windows 上 ProcessExecutor 根本无法工作，必须显式拒绝而不是假装沙箱生效。
_WINDOWS_UNSUPPORTED_REASON = (
    "ProcessExecutor 依赖 POSIX 原语：subprocess 在 Windows 上直接拒绝 preexec_fn"
    "（无法设置进程组与 rlimit），且 os.setsid / os.getpgid / os.killpg / signal.SIGKILL"
    " 在 Windows 上均不存在——超时与取消路径必然抛 AttributeError。"
    "继续执行只会得到一个无进程组隔离、无任何资源上限的子进程。"
)


@dataclass(frozen=True)
class RlimitRequest:
    """一项待施加的 POSIX rlimit（soft 与 hard 取同一个值）。"""

    limit_name: str
    limit_value: int


@dataclass(frozen=True)
class SandboxLimits:
    """本次子进程的沙箱参数（已合并 exec_plan 与 ExecutorConfig 默认值）。"""

    enforce_rlimit: bool
    cpu_seconds: int
    memory_mb: int
    max_open_files: int
    max_processes: int
    file_size_mb: int

    def describe(self) -> str:
        return (
            f"enforce_rlimit={self.enforce_rlimit}, cpu={self.cpu_seconds}s, mem={self.memory_mb}MB, "
            f"nofile={self.max_open_files}, nproc={self.max_processes}, fsize={self.file_size_mb}MB, "
            f"platform={sys.platform}"
        )

    def requested_rlimits(self) -> tuple[RlimitRequest, ...]:
        """把已解析的限制值映射成 rlimit 列表；<=0 表示该项不限制。

        T7-P2-4: ``file_size_mb`` → RLIMIT_FSIZE，防止子进程写单个大文件把
        worker 磁盘打爆。POSIX RLIMIT_FSIZE 只限单文件，配合 artifact_cleanup
        的总量控制形成双层防护。
        """
        if not self.enforce_rlimit:
            return ()
        candidates = (
            ("RLIMIT_CPU", self.cpu_seconds),
            ("RLIMIT_AS", self.memory_mb * _BYTES_PER_MIB),
            ("RLIMIT_NOFILE", self.max_open_files),
            ("RLIMIT_NPROC", self.max_processes),
            ("RLIMIT_FSIZE", self.file_size_mb * _BYTES_PER_MIB),
        )
        return tuple(RlimitRequest(name, value) for name, value in candidates if value > 0)


def require_posix_platform() -> None:
    """B8: Windows 上显式拒绝启动，而不是伪装成沙箱已生效。"""
    if sys.platform == "win32":
        raise RuntimeError(f"当前平台无法安全执行任务: {_WINDOWS_UNSUPPORTED_REASON}")


def preflight_rlimit_support(requested: tuple[RlimitRequest, ...]) -> None:
    """在 fork 之前确认本平台支持所有待施加的 rlimit，不支持就拒绝启动。

    B8: preexec_fn 内部抛出的异常会被 CPython 丢弃，父进程只能拿到一句笼统的
    "Exception occurred in preexec_fn."。因此凡是父进程能提前判定的失败都在这里
    判定，保证错误信息可定位。
    """
    if not requested:
        return
    if not HAS_RESOURCE or resource is None:
        raise RuntimeError("当前平台缺少 resource 模块，无法施加 POSIX 资源限制，拒绝在无限制状态下启动子进程")
    missing = [item.limit_name for item in requested if getattr(resource, item.limit_name, None) is None]
    if missing:
        raise RuntimeError(f"当前平台不支持资源限制项 {', '.join(missing)}，拒绝在无限制状态下启动子进程")


def apply_rlimit(request: RlimitRequest) -> None:
    """在子进程内施加单项 rlimit；失败直接抛出（fail-closed）。

    ``preflight_rlimit_support`` 已保证 ``resource`` 与该常量存在，因此这里
    只可能因内核拒绝而失败（例如 macOS 的 RLIMIT_AS 恒返回 EINVAL）——那正是
    必须让子进程起不来的情况。
    """
    limit_kind = getattr(resource, request.limit_name)
    resource.setrlimit(limit_kind, (request.limit_value, request.limit_value))


def build_preexec_fn(limits: SandboxLimits) -> Callable[[], None]:
    """构造子进程 preexec_fn：独立进程组 + POSIX rlimit。

    B7/B8: 这里的每一步失败都必须让子进程"根本起不来"。
    - ``os.setsid()`` 失败 → 子进程会留在 Worker 自己的进程组里，随后的
      ``killpg`` 会把 Worker 主进程连同所有兄弟任务一起杀掉；
    - ``setrlimit`` 失败 → 子进程以无限制运行，而调用方与 master 毫无感知，
      任务照常上报 SUCCESS。
    两者都直接抛异常：CPython 会在 exec 之前中止子进程，``create_subprocess_exec``
    随之抛错，绝不放行一个没有沙箱的进程。
    """
    require_posix_platform()

    requested = limits.requested_rlimits()
    preflight_rlimit_support(requested)

    def _pre() -> None:
        os.setsid()
        for request in requested:
            apply_rlimit(request)

    return _pre


def host_max_processes(command: list[str], configured_limit: int) -> int:
    """bwrap 会 unshare user namespace，宿主 uid 的 NPROC 上限不再适用。"""
    if command and os.path.basename(command[0]) == _BWRAP_EXECUTABLE:
        return 0
    return configured_limit
