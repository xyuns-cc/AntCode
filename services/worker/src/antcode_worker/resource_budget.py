"""Worker 自适应限额的预算来源：容器自己的 cgroup，而不是宿主 /proc。

自适应限额必须从"这个 Worker 进程真正能用多少"推导。容器里宿主视图与
cgroup 配额相差一个数量级——真机实测：``mem_limit=3g`` 的容器里
``psutil.virtual_memory().total`` 读到 **31.34GiB**（宿主）、``cpu.max`` 是
2 CPU 而 ``nproc`` 读到 **8**。按宿主值算出的 per-task 限额乘以并发会远超容器
额度（实测 8 × 2808MB = 21.9GiB vs 3GiB 容器），后果是任务被容器 cgroup 打到
内存压力区、被 RSS 监控杀掉，而不是被自己的限额干净地拦下。

这与 JVM 读不到 cgroup 按宿主内存定堆尺寸是同一个形状的缺陷：**值从错误的来源
算出来**。

读不到 cgroup 时不假装没事：
- 挂着 cgroup 但不是 v2 → 直接抛错（见 ``_require_cgroup_v2``），本版本只支持
  cgroup v2 统一层级;
- cgroup 文件不存在 → 这个进程确实没有 cgroup 内存上限（裸机部署），宿主总量
  就是正确答案；来源记进 ``origin`` 并由调用方打印，不静默;
- cgroup 文件存在但读不出/解析不了 → 无法判定预算，直接抛错（fail-closed），
  绝不退回宿主值。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

_BYTES_PER_MIB = 1024 * 1024

# cgroup v2 统一层级；容器内有 cgroup namespace，读到的就是本容器自己的额度。
CGROUP_ROOT = Path("/sys/fs/cgroup")
CGROUP_V2_MEMORY_MAX = CGROUP_ROOT / "memory.max"
CGROUP_V2_CPU_MAX = CGROUP_ROOT / "cpu.max"
# 判定 cgroup 代际只认这一个文件：它是 v2 独有的，v1 与 hybrid 都没有。
# 不能拿 memory.max 的缺席来判——缺席既可能是 v1，也可能是压根没挂 cgroup，
# 而这两者要走的路正好相反（前者必须失败，后者宿主值就是答案）。
CGROUP_V2_CONTROLLERS = CGROUP_ROOT / "cgroup.controllers"

# cgroup v2 用字面量 "max" 表示不限制；另外 mem_limit 允许大于宿主物理内存，
# 那种设法同样等于没设限，一并按"不小于宿主物理量即视为不限制"折叠掉。
_CGROUP_UNLIMITED_LITERAL = "max"

# 任务池只拿预算的一部分：Worker 父进程自己要跑 gRPC 长连接、日志缓冲、artifact
# 打包，容器 tmpfs（/tmp 与 ~/.cache）也计进同一个 memory cgroup。留 30% 给它们。
TASK_POOL_SHARE_OF_BUDGET = 0.7


class BudgetSource(StrEnum):
    """预算取自哪一层——必须能在启动日志里被看见。"""

    CGROUP_V2 = "cgroup-v2"
    HOST = "host"


class ResourceBudgetError(RuntimeError):
    """预算无法判定，或生效限额放不进预算。"""


@dataclass(frozen=True)
class MemoryBudget:
    """本 Worker 进程可用的内存总额。"""

    total_bytes: int
    source: BudgetSource
    origin: str

    @property
    def total_mb(self) -> int:
        return self.total_bytes // _BYTES_PER_MIB

    @property
    def task_pool_mb(self) -> int:
        """留给所有并发任务瓜分的额度。"""
        return int(self.total_mb * TASK_POOL_SHARE_OF_BUDGET)

    def describe(self) -> str:
        return f"{self.total_mb}MB(来源: {self.source.value} {self.origin})"


@dataclass(frozen=True)
class CpuBudget:
    """本 Worker 进程可用的 CPU 配额，两种用法一个来源。

    ``cores`` 向下取整、至少 1，用来给并发与运行时定尺寸——半个核开不出半条并发。
    ``quota_cores`` 保留 ``cpu.max`` 的原始小数，只用作 CPU 使用率的分母：
    ``--cpus=1.5`` 的容器拿取整后的 1 核当分母，会把 150% 的过载报成 100%，正好把
    调度门禁需要看见的那一段磨平。两个字段必须由同一次配额解析产出，各算各的就是
    在制造第二个真源。
    """

    cores: int
    quota_cores: float
    source: BudgetSource
    origin: str

    def describe(self) -> str:
        return f"{self.cores}核(来源: {self.source.value} {self.origin})"


def read_cgroup_value(path: Path) -> str | None:
    """读取一个 cgroup 控制文件；文件不存在返回 None，读失败上抛。

    "不存在"与"读不出来"必须区分：前者说明这层 cgroup 压根没生效，后者说明
    预算不可知——后者退回宿主值就是本模块要修的那个 bug。

    对 ``resource_usage`` 公开：额度与占用必须用同一套读法与同一套失败语义，
    各抄一份迟早分叉成"同名双实现"。
    """
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ResourceBudgetError(f"cgroup 文件存在但读取失败，无法判定资源预算: {path}") from exc


def _parse_cgroup_bytes(raw: str, path: Path) -> int | None:
    """把 memory.max 的内容解析成字节数；不限制返回 None。"""
    if raw == _CGROUP_UNLIMITED_LITERAL:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ResourceBudgetError(f"cgroup 内存上限无法解析，拒绝按宿主内存估算: {path} 内容={raw!r}") from exc


def _require_cgroup_v2() -> None:
    """挂着 cgroup 却不是 v2 → 拒绝继续，不允许退回宿主视图。

    这条不是可有可无的告警，而是删掉 v1 读取分支之后唯一的出口：v1/hybrid 宿主上
    ``memory.max`` / ``cpu.max`` 同样不存在，在下面两个探测函数里与"裸机压根没挂
    cgroup"长得一模一样，都会落到 ``BudgetSource.HOST``——限额于是按宿主的 31GiB
    和 8 核算出来，而不是容器的 3GiB / 2 核，正好把本模块存在的那个超卖 bug 原样
    复活，还附赠一句"未检测到 cgroup 上限"让人以为是裸机。所以两者必须在这里分开。

    本仓没有任何 v1 部署目标（无文档、无 compose 声明、无宿主探测脚本、无 v1 真机
    记录），曾经的 v1 读取分支从未在任何宿主上执行过。与其留一条没验证过的路径让人
    以为有防护，不如在这里响亮地失败。
    """
    if not CGROUP_ROOT.exists() or CGROUP_V2_CONTROLLERS.exists():
        return
    raise ResourceBudgetError(
        f"检测到 {CGROUP_ROOT} 已挂载但不是 cgroup v2（{CGROUP_V2_CONTROLLERS} 不存在），"
        "本版本只支持 cgroup v2 统一层级。v1/hybrid 宿主上读不到本容器的额度，"
        "继续运行会按宿主内存与核数算限额并超卖容器额度。"
        "请把宿主切到 cgroup v2（内核启动参数 systemd.unified_cgroup_hierarchy=1）后重启。"
    )


def _cgroup_memory_limit_bytes() -> tuple[int | None, BudgetSource, str]:
    _require_cgroup_v2()
    raw = read_cgroup_value(CGROUP_V2_MEMORY_MAX)
    if raw is None:
        return None, BudgetSource.HOST, f"未检测到 cgroup 内存上限(已探测 {CGROUP_V2_MEMORY_MAX})"
    return _parse_cgroup_bytes(raw, CGROUP_V2_MEMORY_MAX), BudgetSource.CGROUP_V2, str(CGROUP_V2_MEMORY_MAX)


def resolve_memory_budget(host_total_bytes: int) -> MemoryBudget:
    """优先用 cgroup 上限；没有 cgroup 上限时宿主总量才是正确答案。"""
    limit, source, origin = _cgroup_memory_limit_bytes()
    if limit is None or limit >= host_total_bytes:
        # limit >= 宿主物理内存 = cgroup 没有真正设限，宿主总量才是真正的天花板。
        host_origin = origin if source is BudgetSource.HOST else f"{origin} 未设限"
        return MemoryBudget(total_bytes=host_total_bytes, source=BudgetSource.HOST, origin=host_origin)
    return MemoryBudget(total_bytes=limit, source=source, origin=origin)


def _cgroup_cpu_cores() -> tuple[float | None, BudgetSource, str]:
    _require_cgroup_v2()
    raw = read_cgroup_value(CGROUP_V2_CPU_MAX)
    if raw is None:
        return None, BudgetSource.HOST, f"未检测到 cgroup CPU 配额(已探测 {CGROUP_V2_CPU_MAX})"
    return _parse_cpu_max(raw), BudgetSource.CGROUP_V2, str(CGROUP_V2_CPU_MAX)


def _parse_cpu_max(raw: str) -> float | None:
    """``"<quota> <period>"``；quota 为字面量 ``max`` 表示不限制。"""
    fields = raw.split()
    expected_fields = 2
    if len(fields) != expected_fields:
        raise ResourceBudgetError(f"cgroup CPU 配额无法解析，拒绝按宿主核数估算: 内容={raw!r}")
    quota_raw, period_raw = fields
    if quota_raw == _CGROUP_UNLIMITED_LITERAL:
        return None
    try:
        quota, period = int(quota_raw), int(period_raw)
    except ValueError as exc:
        raise ResourceBudgetError(f"cgroup CPU 配额无法解析，拒绝按宿主核数估算: 内容={raw!r}") from exc
    if quota <= 0 or period <= 0:
        # v2 的 cpu.max 只写 "max" 或正整数，非正数只可能是文件坏了。这里原本返回
        # None（当作不限制、退回宿主核数），那是 v1 拿 -1 表示不限制留下的读法：
        # 沿用它会让一个坏文件静默变成"按宿主 8 核定并发"，且 quota_cores=0 会在
        # CPU 使用率的分母上直接除零。
        raise ResourceBudgetError(f"cgroup CPU 配额不是正数，拒绝按宿主核数估算: 内容={raw!r}")
    return quota / period


def resolve_cpu_budget(host_cpu_count: int) -> CpuBudget:
    """cgroup CPU 配额优先；配额不足 1 核时向上取 1（并发下限就是 1）。"""
    cores, source, origin = _cgroup_cpu_cores()
    if cores is None or cores >= host_cpu_count:
        host_origin = origin if source is BudgetSource.HOST else f"{origin} 未设限"
        host_cores = max(1, host_cpu_count)
        return CpuBudget(
            cores=host_cores,
            quota_cores=float(host_cores),
            source=BudgetSource.HOST,
            origin=host_origin,
        )
    return CpuBudget(cores=max(1, int(cores)), quota_cores=cores, source=source, origin=origin)


def validate_capacity_fits_budget(
    *,
    max_concurrent_tasks: int,
    task_memory_limit_mb: int,
    budget: MemoryBudget,
) -> None:
    """并发 × 单任务限额必须放得进任务池，否则拒绝启动。

    自适应路径按生效并发做除法，天然满足；这条校验兜的是**手动值**——手动把
    并发或单任务限额调大就能绕开自适应，那才是真正会把容器额度打爆的配置。
    失败必须是显式异常：静默超卖的表现是"任务被容器 cgroup 杀掉"，运维只会看到
    莫名其妙的 exit -9，根本追不到配置上。
    """
    required_mb = max_concurrent_tasks * task_memory_limit_mb
    pool_mb = budget.task_pool_mb
    if required_mb <= pool_mb:
        return
    raise ResourceBudgetError(
        f"资源限额超卖: max_concurrent_tasks={max_concurrent_tasks} × "
        f"task_memory_limit_mb={task_memory_limit_mb}MB = {required_mb}MB，"
        f"超过任务池 {pool_mb}MB（预算 {budget.describe()}，任务池占比 {TASK_POOL_SHARE_OF_BUDGET}）。"
        f"请调小并发或单任务限额，或提高容器 mem_limit。"
    )


__all__ = [
    "TASK_POOL_SHARE_OF_BUDGET",
    "BudgetSource",
    "CpuBudget",
    "MemoryBudget",
    "ResourceBudgetError",
    "read_cgroup_value",
    "resolve_cpu_budget",
    "resolve_memory_budget",
    "validate_capacity_fits_budget",
]
