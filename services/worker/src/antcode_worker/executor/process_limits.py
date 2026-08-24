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

# 内存上限映射到 RLIMIT_DATA 而不是 RLIMIT_AS。
#
# RLIMIT_AS 限的是**虚拟地址空间**，而现代运行时会 PROT_NONE 预留远超实际用量的
# 地址区间（预留不占物理页）：JVM 光 compressed class space 就保留 1GiB，tsx 的
# V8 WASM 要 32~64GB。按"预留量"收费的结果是——真机实测，同一 bwrap 画像下同一个
# 用例：Java 在 RLIMIT_AS 2808MB（31GB/8 核机器的自适应默认值）起不来 JVM，
# RLIMIT_DATA 128MB 就能跑；TypeScript 在 RLIMIT_AS 32768MB 仍 OOM、要 65536MB
# 才过，而 task_memory_limit_mb 的 API 上限只有 8192MB，即抬上限根本无解，
# RLIMIT_DATA 256MB 直接通过。
#
# RLIMIT_DATA 自 Linux 4.7 起覆盖 brk 与私有可写映射，PROT_NONE 预留不计入，
# 因此收的是"真的会写下去的内存"。
#
# 语义代价（必须知情）：MAP_SHARED 匿名映射与 tmpfs 页不计入 RLIMIT_DATA，
# 而 RLIMIT_AS 会计入。但这两类页**不由同一层兜底**，写成一句会得到一个不存在的防护：
#
# - 被映射进地址空间的页（MAP_SHARED 匿名映射、mmap 出来的 tmpfs 页）计入
#   ``memory_info().rss``，进程树 RSS 监控看得见，超限直接杀进程组。真机实测
#   （192.168.1.250 / 限额 1433MB）：MAP_SHARED 写满 2000MB，采到树 RSS 2013MB，
#   判定成立。
# - ``write()`` 写进 tmpfs 的页**没有任何进程映射它，因此不进任何进程的 RSS**，
#   进程树监控对这条路径完全失明。同一画像下 dd 往沙箱 /tmp 写 3000MB（2.09×限额）：
#   任务树 RSS 全程 ≤6.1MB、exit 0 上报 SUCCESS，而容器 memory.current 冲到 2816MB。
#   真正约束它的是沙箱 tmpfs 的 ``--size``（见 ``sandbox_mounts``，按本任务的内存
#   限额定尺寸）与容器级 mem_limit——与 RSS 监控无关。
#
# 容器内 /sys/fs/cgroup 是只读挂载且 cgroup.subtree_control 为空，
# 在现有安全画像（cap_drop ALL / no-new-privileges / 非 root）下无法为每个任务
# 创建 cgroup，所以"每任务内存硬限额"没有内核级实现。
_MEMORY_RLIMIT_NAME = "RLIMIT_DATA"

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
        # 点名内存用的是哪一项 rlimit：它决定了"限住的是什么"（可写数据段而非
        # 虚拟地址空间），排障时不写清楚会把人引向错误的量级判断。
        return (
            f"enforce_rlimit={self.enforce_rlimit}, cpu={self.cpu_seconds}s, "
            f"mem={self.memory_mb}MB({_MEMORY_RLIMIT_NAME}), "
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
            (_MEMORY_RLIMIT_NAME, self.memory_mb * _BYTES_PER_MIB),
            ("RLIMIT_NOFILE", self.max_open_files),
            ("RLIMIT_NPROC", self.max_processes),
            ("RLIMIT_FSIZE", self.file_size_mb * _BYTES_PER_MIB),
        )
        return tuple(RlimitRequest(name, value) for name, value in candidates if value > 0)


def effective_memory_limit_mb(plan_limit_mb: int, default_limit_mb: int) -> int:
    """本次执行真正生效的内存限额：计划值优先，0 表示"本次未指定"，退到执行器默认值。

    进程层（RLIMIT_DATA + 进程树 RSS 监控）与沙箱层（tmpfs ``--size``）必须用**同一个**
    数。两处各写一遍 ``a or b``，改动其一就会分叉成"rlimit 按 1433MB 收、tmpfs 按另一个
    数切"——而这种分叉在真机上的表现是"限额看起来生效了却拦不住"，正是本仓反复出现的
    同名双实现。

    两个值都是 0 表示运维显式关掉了内存限额；此时没有任何任务级数值可以用来给 tmpfs
    定尺寸，调用方据此**不下 --size**，而不是另编一个数。
    """
    return plan_limit_mb or default_limit_mb


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
    只可能因内核拒绝而失败（例如 macOS 上 RLIMIT_DATA / RLIMIT_AS 同样拒绝下调，
    抛 "current limit exceeds maximum limit"）——那正是必须让子进程起不来的情况。
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
