"""本 Worker 现在占用了多少——与 ``resource_budget`` 的额度配对的那个分子。

``resource_budget`` 回答"这个 Worker 能用多少"，本模块回答"它现在用了多少"。
两个数必须取自**同一层**，否则算出来的资源页自相矛盾：额度取容器 cgroup(3GiB)、
占用取宿主 psutil(12GiB)，页面会显示"已用 12GiB / 总计 3GiB、可用 0"，比原来
全宿主视图更糟。所以本模块的入口直接吃 ``MemoryBudget``，由它的 ``source``
决定占用从哪一层读——**来源不可能分叉**。

宿主视图与容器额度的量级差见 ``resource_budget`` 的模块注释（真机实测同一台
Worker：宿主 31.34GiB vs 容器 mem_limit 3GiB）。资源页拿宿主数字做容量规划会
高估一个数量级，这与 JVM 读不到 cgroup 按宿主内存定堆尺寸是同一形状的缺陷。

**占用的口径**与 ``docker stats`` / cAdvisor 一致：``memory.current`` 扣掉
``inactive_file``。inactive_file 是内核在内存压力下直接回收的页缓存，把它算成
"已用"会让任何读写过文件的容器常年显示逼近上限。这不是我们发明的比例，是内核
对 working set 的既有定义——真机对照：worker3 的 memory.current=70201344、
inactive_file=4096，``docker stats`` 报 66.37MiB/3GiB，两者同口径。

失败语义沿用 ``resource_budget``：额度已判定为 cgroup 却读不到对应的占用文件，
说明这两层对不上，直接抛（fail-closed），绝不退回宿主占用。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from antcode_worker.resource_budget import (
    BudgetSource,
    MemoryBudget,
    ResourceBudgetError,
    read_cgroup_value,
)

_BYTES_PER_MIB = 1024 * 1024
_PERCENT_SCALE = 100.0
_PERCENT_DIGITS = 1
_MB_DIGITS = 1

# cgroup v2 统一层级；非 v2 宿主在 resource_budget._require_cgroup_v2 就被拦下了。
CGROUP_V2_MEMORY_CURRENT = Path("/sys/fs/cgroup/memory.current")
CGROUP_V2_MEMORY_STAT = Path("/sys/fs/cgroup/memory.stat")

# memory.stat 里那部分内核随时能回收、因此不该算进"已用"的页缓存。
_RECLAIMABLE_KEY = "inactive_file"


@dataclass(frozen=True, slots=True)
class HostMemory:
    """``psutil.virtual_memory()`` 的宿主视图快照。

    以参数注入而不是在本模块 import psutil：与 ``resolve_memory_budget`` 吃
    ``host_total_bytes`` 同一形状，也让 cgroup 分支能在没有 cgroup 的开发机上测。
    """

    total_bytes: int
    used_bytes: int
    available_bytes: int
    percent: float


@dataclass(frozen=True, slots=True)
class MemoryUsage:
    """同一层里的总额与占用。四个数一起产出，避免各自推导出互相矛盾的结果。"""

    total_bytes: int
    used_bytes: int
    available_bytes: int
    percent: float
    source: BudgetSource

    @property
    def total_mb(self) -> float:
        return round(self.total_bytes / _BYTES_PER_MIB, _MB_DIGITS)

    @property
    def used_mb(self) -> float:
        return round(self.used_bytes / _BYTES_PER_MIB, _MB_DIGITS)

    @property
    def available_mb(self) -> float:
        return round(self.available_bytes / _BYTES_PER_MIB, _MB_DIGITS)


def _parse_bytes(raw: str, path: Path) -> int:
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise ResourceBudgetError(f"cgroup 内存占用无法解析，拒绝按宿主占用估算: {path} 内容={raw!r}") from exc


def _require_cgroup_value(path: Path, source: BudgetSource) -> str:
    raw = read_cgroup_value(path)
    if raw is None:
        raise ResourceBudgetError(
            f"内存额度已判定为 {source.value}，却读不到同层的占用文件 {path}。"
            "额度与占用必须同源，退回宿主占用会算出'已用 12GiB / 总计 3GiB'这种资源页。"
        )
    return raw


def _reclaimable_bytes(source: BudgetSource) -> int:
    raw = _require_cgroup_value(CGROUP_V2_MEMORY_STAT, source)
    for line in raw.splitlines():
        name, _, value = line.partition(" ")
        if name == _RECLAIMABLE_KEY:
            return _parse_bytes(value, CGROUP_V2_MEMORY_STAT)
    raise ResourceBudgetError(
        f"{CGROUP_V2_MEMORY_STAT} 里没有 {_RECLAIMABLE_KEY}，"
        "无法按 working set 口径算内存占用（拒绝改用会虚高的 memory.current 原值）。"
    )


def _cgroup_used_bytes(source: BudgetSource) -> int:
    raw = _require_cgroup_value(CGROUP_V2_MEMORY_CURRENT, source)
    return _parse_bytes(raw, CGROUP_V2_MEMORY_CURRENT) - _reclaimable_bytes(source)


def resolve_memory_usage(budget: MemoryBudget, host: HostMemory) -> MemoryUsage:
    """按额度的来源决定占用的来源；两者永远同层。

    额度来自宿主（裸机部署，或 cgroup 存在但没设限）时，psutil 的宿主视图就是
    正确答案，原样透传——包括它自己的 ``percent``/``available``，因为宿主的
    "可用"含可回收缓存，不等于 ``total - used``，我们不在这里替它重算。
    """
    if budget.source is BudgetSource.HOST:
        return MemoryUsage(
            total_bytes=host.total_bytes,
            used_bytes=host.used_bytes,
            available_bytes=host.available_bytes,
            percent=host.percent,
            source=BudgetSource.HOST,
        )
    used = _cgroup_used_bytes(budget.source)
    total = budget.total_bytes
    return MemoryUsage(
        total_bytes=total,
        used_bytes=used,
        available_bytes=total - used,
        percent=round(used * _PERCENT_SCALE / total, _PERCENT_DIGITS),
        source=budget.source,
    )


__all__ = [
    "HostMemory",
    "MemoryUsage",
    "resolve_memory_usage",
]
