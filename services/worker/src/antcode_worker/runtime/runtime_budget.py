"""把任务的真实资源预算翻译成各语言运行时看得懂的旋钮。

沙箱里的运行时**发现不了自己的预算**：bwrap 的 mount namespace 里根本没有 ``/sys``
（``sandbox_mounts._SYSTEM_ROOTS`` 只挂 /usr /bin /sbin /lib /lib64），``/sys/fs/cgroup``
整个不存在；而 procfs 的 meminfo/cpuinfo 本来就不按 namespace 区分，``--proc`` 重挂
一份读到的仍是**宿主**的数字。真机实测（宿主 32GB/8 核，容器 ``mem_limit=3g``、
``cpus=2``，同一份二进制）：

===========================  ==================  =================
指标                         容器内(能读 cgroup)  bwrap 内(读不到)
===========================  ==================  =================
JVM InitialHeapSize                 48 MiB             504 MiB
JVM MaxHeapSize                    768 MiB            8024 MiB
Go  GOMAXPROCS                        2                   8
V8  heap_size_limit                1584 MB            4144 MB
===========================  ==================  =================

而 ``RLIMIT_DATA`` 是按**容器预算 ÷ 并发**算出来的（3GiB×70%÷4 = 537MB）。两个数字
之间没有任何因果关系：JVM 光初始堆就要 504MiB，在 537MB 的 RLIMIT_DATA 下必然
``os::commit_memory ... errno=12`` 起不来。

这条不能靠调大限额绕过：门槛 ≈ 宿主RAM/64 + JVM 自身开销，**随生产宿主内存上涨**
（32GB 宿主实测 610MB，64GB 约 1.15GB，128GB 约 2.1GB），"测试机过了"推不出生产会过。
唯一的修法是把预算**告诉**运行时，让它自己的 ergonomics 按正确的数算。

三个旋钮都不引入我们自己发明的比例：Java/Go 直接复用运行时在能看见 cgroup 时对
同一个数字做的事，Node 只在 V8 没有等价旋钮时才落到一个有实测依据的份额上。
"""

from __future__ import annotations

from dataclasses import dataclass

from antcode_worker.adaptive_limits import current_cpu_budget

_BYTES_PER_MIB = 1024 * 1024

# V8 没有 "-XX:MaxRAM" 那样的"告诉我这台机器多大"旋钮，只能直接给老生代上限。
# V8 自己在能读到 cgroup 时把老生代定在受限内存的约 51.6%（真机实测：受限 3072MB →
# heap_size_limit 1584MB）。取 1/2 是这个比例的稳定表达——V8 的分档函数随版本变，
# 抄它当前的档位等于把一个版本相关的常量钉进产品。剩下的一半留给新生代半空间、
# code space 与 ArrayBuffer 等外部分配：它们同样计入 RLIMIT_DATA，却不在
# ``--max-old-space-size`` 的账里。
_V8_OLD_SPACE_SHARE_OF_BUDGET = 2


class RuntimeBudgetUnknownError(RuntimeError):
    """本次任务的生效内存限额不可知，拒绝启动一个按宿主尺寸自我配置的运行时。"""


@dataclass(frozen=True)
class RuntimeBudget:
    """一次任务执行真正能用的资源，也就是运行时**应该**读到的那两个数。

    ``memory_mb`` 与 ``RLIMIT_DATA`` 同源（都是 ``RunContext.memory_limit_mb``），
    所以运行时按它定尺寸时，定出来的东西一定放得进限额。
    """

    memory_mb: int
    cpu_cores: int

    @property
    def memory_bytes(self) -> int:
        return self.memory_mb * _BYTES_PER_MIB


def resolve_runtime_budget(memory_limit_mb: int) -> RuntimeBudget:
    """由生效的单任务内存限额构造预算；限额不可知即显式失败。

    ``memory_limit_mb <= 0`` 不是"不限制"：``init_worker_config`` 恒把 0 换成自适应
    或默认限额，``Engine`` 再把 ``policies.resource.memory_limit_mb`` 原样写进
    ``RunContext``，所以这里读到 0 只可能是上游接线断了。此时"安静地不注入"会让
    JVM 按宿主 32GB 定尺寸、随后被容器 cgroup 打死——正是本模块要消灭的那一类
    "值来自错误来源"的静默失败，因此必须抛。
    """
    if memory_limit_mb <= 0:
        raise RuntimeBudgetUnknownError(
            f"单任务内存限额不可知({memory_limit_mb})，无法把预算告诉沙箱内的运行时。"
            "沙箱内没有 /sys/fs/cgroup、/proc/meminfo 又是宿主视图，运行时会按宿主内存"
            "定堆尺寸并在容器额度内被杀。请检查 Worker 资源限额接线"
            "(config.init_worker_config → Engine.policies.resource → RunContext)。"
        )
    # CPU 配额与自适应限额同源（``adaptive_limits.current_cpu_budget``）：Go 在能看见
    # cgroup 时算出的就是这个数，两处各读一份迟早会分叉。
    return RuntimeBudget(memory_mb=memory_limit_mb, cpu_cores=current_cpu_budget().cores)


def java_runtime_argv(budget: RuntimeBudget) -> tuple[str, ...]:
    """JVM 侧的预算注入，必须排在 ``-jar`` 之前才会被当成 VM 参数。

    只给 ``-XX:MaxRAM``，不自己算 ``-Xmx``/``-Xms``：MaxRAM 的语义就是"你可以认为
    这台机器有这么多内存"，JVM 拿到它之后会用**与它在容器里读到 cgroup 上限时完全
    相同**的 ergonomics 定初始堆(MaxRAM/64)与最大堆(MaxRAM/4)。真机对照：容器 3GiB
    → 48MiB/768MiB；MaxRAM=537MB → 10MiB/136MiB，同一组比例。自己再乘一个安全系数
    等于在 JVM 已有的比例之上叠第二套，既无从解释也会随 JVM 版本失配。

    只压初始堆（如 ``-Xms16m``）是不够的：真机实测那样做 MaxHeapSize 仍是宿主的
    8024MiB，任务起得来但一分配就冲过限额，被 RSS 监控杀掉而不是收到一个干净的
    ``java.lang.OutOfMemoryError``。上限和初始值必须同源。
    """
    return (f"-XX:MaxRAM={budget.memory_bytes}",)


def node_runtime_argv(budget: RuntimeBudget) -> tuple[str, ...]:
    """Node 侧的预算注入，必须排在脚本路径之前才会被当成 V8 参数。"""
    return (f"--max-old-space-size={budget.memory_mb // _V8_OLD_SPACE_SHARE_OF_BUDGET}",)


def go_budget_env(budget: RuntimeBudget) -> dict[str, str]:
    """Go 侧的预算注入：把容器 CPU 配额补给读不到 cgroup 的沙箱内 Go。

    Go 1.25+ 自己就会读 cgroup 的 ``cpu.max`` 定 GOMAXPROCS（真机实证：同一二进制
    在容器里是 2、在 bwrap 里是 8）。这里交回去的就是它自己会算出的那个数，没有引入
    任何新比例。GOMAXPROCS 同时决定 ``go build`` 的 ``-p``（并行编译动作数）与
    ``cmd/compile`` 的后端并发 ``-c``，而这两个才是编译期内存的乘数——真机实测
    ``go build`` 整棵进程树峰值 RSS 从 330MB(GOMAXPROCS=8) 掉到 78MB(=2)。

    用**容器配额**而不是"配额 ÷ 并发"：CPU 是时间片，超卖只会变慢不会失败，而
    Go 在容器里本来就按容器配额定 GOMAXPROCS，除以并发是我们发明的、Go 从不做的
    动作。内存正相反——RLIMIT_DATA 是逐进程硬限，只能按任务份额给。
    """
    return {"GOMAXPROCS": str(budget.cpu_cores)}


__all__ = [
    "RuntimeBudget",
    "RuntimeBudgetUnknownError",
    "go_budget_env",
    "java_runtime_argv",
    "node_runtime_argv",
    "resolve_runtime_budget",
]
