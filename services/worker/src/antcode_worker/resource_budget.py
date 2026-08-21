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
CGROUP_V2_MEMORY_MAX = Path("/sys/fs/cgroup/memory.max")
CGROUP_V2_CPU_MAX = Path("/sys/fs/cgroup/cpu.max")
# cgroup v1 回退路径（老内核 / 老 dockerd）。
CGROUP_V1_MEMORY_LIMIT = Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")
CGROUP_V1_CPU_QUOTA = Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
CGROUP_V1_CPU_PERIOD = Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us")

# cgroup v2 用字面量 "max" 表示不限制；v1 用一个接近 2^63 的哨兵值，
# 两者统一按"不小于宿主物理量即视为不限制"折叠掉。
_CGROUP_UNLIMITED_LITERAL = "max"

# 任务池只拿预算的一部分：Worker 父进程自己要跑 gRPC 长连接、日志缓冲、artifact
# 打包，容器 tmpfs（/tmp 与 ~/.cache）也计进同一个 memory cgroup。留 30% 给它们。
TASK_POOL_SHARE_OF_BUDGET = 0.7


class BudgetSource(StrEnum):
    """预算取自哪一层——必须能在启动日志里被看见。"""

    CGROUP_V2 = "cgroup-v2"
    CGROUP_V1 = "cgroup-v1"
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
    """把 memory.max / memory.limit_in_bytes 的内容解析成字节数；不限制返回 None。"""
    if raw == _CGROUP_UNLIMITED_LITERAL:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ResourceBudgetError(f"cgroup 内存上限无法解析，拒绝按宿主内存估算: {path} 内容={raw!r}") from exc


def _cgroup_memory_limit_bytes() -> tuple[int | None, BudgetSource, str]:
    for path, source in (
        (CGROUP_V2_MEMORY_MAX, BudgetSource.CGROUP_V2),
        (CGROUP_V1_MEMORY_LIMIT, BudgetSource.CGROUP_V1),
    ):
        raw = read_cgroup_value(path)
        if raw is None:
            continue
        return _parse_cgroup_bytes(raw, path), source, str(path)
    return None, BudgetSource.HOST, f"未检测到 cgroup 内存上限(已探测 {CGROUP_V2_MEMORY_MAX}, {CGROUP_V1_MEMORY_LIMIT})"


def resolve_memory_budget(host_total_bytes: int) -> MemoryBudget:
    """优先用 cgroup 上限；没有 cgroup 上限时宿主总量才是正确答案。"""
    limit, source, origin = _cgroup_memory_limit_bytes()
    if limit is None or limit >= host_total_bytes:
        # limit >= 宿主物理内存 = cgroup 没有真正设限（v1 哨兵值走这条）。
        host_origin = origin if source is BudgetSource.HOST else f"{origin} 未设限"
        return MemoryBudget(total_bytes=host_total_bytes, source=BudgetSource.HOST, origin=host_origin)
    return MemoryBudget(total_bytes=limit, source=source, origin=origin)


def _cgroup_cpu_cores() -> tuple[float | None, BudgetSource, str]:
    raw_v2 = read_cgroup_value(CGROUP_V2_CPU_MAX)
    if raw_v2 is not None:
        return _parse_cpu_max(raw_v2), BudgetSource.CGROUP_V2, str(CGROUP_V2_CPU_MAX)
    quota = read_cgroup_value(CGROUP_V1_CPU_QUOTA)
    period = read_cgroup_value(CGROUP_V1_CPU_PERIOD)
    if quota is not None and period is not None:
        return _parse_cpu_max(f"{quota} {period}"), BudgetSource.CGROUP_V1, str(CGROUP_V1_CPU_QUOTA)
    return None, BudgetSource.HOST, f"未检测到 cgroup CPU 配额(已探测 {CGROUP_V2_CPU_MAX}, {CGROUP_V1_CPU_QUOTA})"


def _parse_cpu_max(raw: str) -> float | None:
    """``"<quota> <period>"``；quota 为 max 或负数表示不限制。"""
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
        return None
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
