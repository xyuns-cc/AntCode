"""本 Worker 的 CPU 现在用掉了配额的几成——与 ``resource_usage`` 的内存占用配对。

``resource_budget`` 回答"这个 Worker 能用多少 CPU"，本模块回答"它现在用了多少"。
和内存那一对一样，两个数必须取自**同一层**：额度取容器 ``cpu.max``（2 核）、占用取
宿主 ``psutil.cpu_percent()``（整机 8 核的忙闲程度），除出来的百分比既不是本容器的
压力也不是宿主的压力，而是两层混出来的第三个数。所以本模块的入口直接吃
``CpuBudget``，由它的 ``source`` 决定占用从哪一层读——**来源不可能分叉**。

这个混用不是显示瑕疵，它是调度判据：``worker_dispatcher.is_worker_available`` 拿
CPU 百分比当硬门禁（阈值 90），``calculate_load_score`` 给它 0.30 权重（五项里最高）。
真机实测（192.168.1.250）两个方向都会判错：

- 漏判：``antcode-mn-worker2`` 打满自己 2 核配额时容器口径 **99.6%**、内核已经在限流
  （``nr_throttled`` 0→85、``throttled_usec`` 0→8.58s），宿主口径却只有 **36.0%**，
  门禁完全看不见，调度器继续往一台已被限流的容器上派活；
- 误判：同一时刻真正空闲的 ``antcode-worker`` / ``antcode-mn-worker3``（容器 0.1% /
  0.4%）被记在同一个 36.0% 名下——吵闹邻居把空闲 Worker 一起挤出候选。同一台宿主上
  三个 Worker 报**同一个** CPU 数字，本身就说明这个值与"哪台 Worker"无关。

cgroup 给的是累计 CPU 时间（``cpu.stat`` 的 ``usage_usec``）而不是瞬时百分比，要跨
两次采样求差。这不是新机制：同一条心跳链上的 ``NetworkRateProbe`` 就是这么算速率的，
而被本模块替换掉的 ``psutil.cpu_percent(interval=None)`` 本身也是拿本次与上次
``/proc/stat`` 的差值——差分一直都在，变的只是**从哪一层取数**。

失败语义沿用 ``resource_budget``：额度已判定为 cgroup 却读不到同层的用量文件，说明
这两层对不上，直接抛（fail-closed），绝不退回宿主使用率。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from antcode_worker.resource_budget import (
    BudgetSource,
    CpuBudget,
    ResourceBudgetError,
    read_cgroup_value,
)

_PERCENT_SCALE = 100.0
_PERCENT_DIGITS = 1
_USEC_PER_SEC = 1_000_000.0
_NSEC_PER_SEC = 1_000_000_000.0

# cgroup v2 统一层级。
CGROUP_V2_CPU_STAT = Path("/sys/fs/cgroup/cpu.stat")
# cgroup v1 回退路径（老内核 / 老 dockerd），与 resource_budget 的 v1 分支配套。
# v1 的用量归 cpuacct 控制器，且以纳秒计——与 v2 的微秒不是同一个单位。
CGROUP_V1_CPUACCT_USAGE = Path("/sys/fs/cgroup/cpuacct/cpuacct.usage")

_V2_USAGE_KEY = "usage_usec"


@dataclass(frozen=True, slots=True)
class CpuUsage:
    """一个窗口内的 CPU 使用率，以及它取自哪一层。"""

    percent: float
    source: BudgetSource


def _require_cgroup_value(path: Path, source: BudgetSource) -> str:
    raw = read_cgroup_value(path)
    if raw is None:
        raise ResourceBudgetError(
            f"CPU 额度已判定为 {source.value}，却读不到同层的用量文件 {path}。"
            "额度与用量必须同源，退回宿主使用率等于把整机忙闲程度当成本容器的压力。"
        )
    return raw


def _parse_int(raw: str, path: Path) -> int:
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise ResourceBudgetError(f"cgroup CPU 用量无法解析，拒绝按宿主使用率估算: {path} 内容={raw!r}") from exc


def _v2_used_seconds() -> float:
    raw = _require_cgroup_value(CGROUP_V2_CPU_STAT, BudgetSource.CGROUP_V2)
    for line in raw.splitlines():
        name, _, value = line.partition(" ")
        if name == _V2_USAGE_KEY:
            return _parse_int(value, CGROUP_V2_CPU_STAT) / _USEC_PER_SEC
    raise ResourceBudgetError(
        f"{CGROUP_V2_CPU_STAT} 里没有 {_V2_USAGE_KEY}，算不出本容器的 CPU 用量（拒绝改报宿主使用率）。"
    )


def _v1_used_seconds() -> float:
    raw = _require_cgroup_value(CGROUP_V1_CPUACCT_USAGE, BudgetSource.CGROUP_V1)
    return _parse_int(raw, CGROUP_V1_CPUACCT_USAGE) / _NSEC_PER_SEC


def _cgroup_used_seconds(source: BudgetSource) -> float:
    """用量文件必须与额度所在的那一代 cgroup 严格对应。"""
    if source is BudgetSource.CGROUP_V2:
        return _v2_used_seconds()
    return _v1_used_seconds()


class ContainerCpuSampler:
    """累计计数 → 使用率要跨采样求差，因此采样器自己持有上一份计数。

    与 ``NetworkRateProbe`` 同一形状：状态归探针，探针归采集器，一个 Worker 进程
    一份，这样窗口就是两次心跳之间的真实间隔。
    """

    def __init__(self) -> None:
        self._last: tuple[float, float] | None = None

    def sample(self, budget: CpuBudget, host_percent: float) -> CpuUsage:
        """按额度的来源决定使用率的来源；两者永远同层。

        额度来自宿主（裸机部署，或 cgroup 存在但没设限）时，psutil 的整机使用率就是
        正确答案，原样透传——与 ``resolve_memory_usage`` 的 HOST 分支同一处理。
        """
        if budget.source is BudgetSource.HOST:
            return CpuUsage(percent=host_percent, source=BudgetSource.HOST)
        return self._cgroup_percent(budget)

    def _cgroup_percent(self, budget: CpuBudget) -> CpuUsage:
        used_seconds = _cgroup_used_seconds(budget.source)
        now = time.monotonic()
        previous = self._last
        self._last = (used_seconds, now)
        if previous is None:
            # 进程内首次采样没有窗口可求差。被本模块替换掉的
            # ``psutil.cpu_percent(interval=None)`` 首次调用同样返回 0.0，所以这不是
            # 新开的洞；下一次心跳就是真值。这里绝不拿宿主使用率顶替。
            return CpuUsage(percent=0.0, source=budget.source)
        last_used, last_at = previous
        percent = _PERCENT_SCALE * (used_seconds - last_used) / ((now - last_at) * budget.quota_cores)
        return CpuUsage(percent=round(percent, _PERCENT_DIGITS), source=budget.source)


__all__ = [
    "CGROUP_V1_CPUACCT_USAGE",
    "CGROUP_V2_CPU_STAT",
    "ContainerCpuSampler",
    "CpuUsage",
]
