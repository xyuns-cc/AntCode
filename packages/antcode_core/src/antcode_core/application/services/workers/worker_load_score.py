"""Worker 负载评分：把"离自己那份预算的上限还有多远"折成一个可排序的数（越低越优）。

从 ``worker_dispatcher`` 拆出来的直接原因是这里曾有两项判据**恒定**，而恒定判据不是
"不起作用"——它把其余判据的**实际**权重悄悄改掉了：

- ``latency`` 取的是 ``now - worker.last_heartbeat`` 的毫秒数（心跳年龄），不是网络
  往返。心跳间隔默认 30s（``WORKER_HEARTBEAT_INTERVAL``），采样到的年龄几乎总是
  ≥1000ms，而原式在 ``latency >= 1000`` 处直接取 100 分，于是恒定饱和；
- ``successRate`` 读的是顶层 ``metrics["successRate"]``，全仓无写入方（心跳 proto 的
  ``Metrics`` 没有这个字段，``_redis_metrics`` 的映射白名单也没有），恒取默认值 100，
  ``100 - 100 = 0``，恒定不贡献。

两项加起来 0.25 的名义权重全是常数，剩下三项在 0.75 上按比例放大：CPU 的 0.30 实际
是 0.40。刚把 CPU 与内存双双换成容器口径（f23f192 / 8074a8f）之后，这个放大作用在
一个比设计时更敏感的量上。

``latency`` 还不只是常数：它在 999ms→1000ms 处从 ~50 跳到 100（分段函数与对数段接不
上），所以偶尔"心跳刚落地"的那台会白拿最多 15 分的优势——与它的忙闲无关。噪声，不是
判据。两项都已从算式中删除，理由分别记在下面的常量旁。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

# 指标全部是百分比口径；"满"既是 100 也是"取不到数时按最坏算"的取值。
PERCENT_FULL = 100.0
_SCORE_DIGITS = 2


def _cpu_saturation(metrics: Mapping[str, Any]) -> float:
    """本容器 CPU 配额已用掉的百分比（f23f192 之后不再是宿主整机忙闲）。"""
    return float(metrics.get("cpu", PERCENT_FULL))


def _memory_saturation(metrics: Mapping[str, Any]) -> float:
    """本容器内存额度已用掉的百分比（8074a8f 之后不再是宿主 /proc/meminfo）。"""
    return float(metrics.get("memory", PERCENT_FULL))


def _task_saturation(metrics: Mapping[str, Any]) -> float:
    """并发槽位已占掉的百分比：运行中 + 排队中，对 ``maxConcurrentTasks``。"""
    max_tasks = int(metrics.get("maxConcurrentTasks", 0))
    if max_tasks <= 0:
        return PERCENT_FULL
    occupied = int(metrics.get("runningTasks", 0)) + int(metrics.get("queuedTasks", 0))
    return min(PERCENT_FULL, occupied / max_tasks * PERCENT_FULL)


# 三项判据现在是同一件事的三个切面——"这台已经吃掉了自己那份预算的百分之几"。
# CPU 与内存换成容器配额口径之后，它们与"并发槽位占用率"才第一次可比：之前前两项是
# 整台宿主的忙闲（同宿主上几个 Worker 报同一个数），与后一项根本不是同一个坐标系。
#
# 权重按项数均分，理由是**我们没有能支持任何不均分法的测量**：没有数据说明 CPU 比内存
# 更能预测"再派一个任务会不会拖慢它"。原来的 0.30/0.25/0.20 出自 70a26e9 的裸字面量，
# 同一个类里还并存一份对不上的 WEIGHT_* 常量（0.3/0.3/0.25/0.15，且从未被引用）——
# 连作者都有两个版本，说明它不是算出来的，是拍的。拍的数不该继承。
#
# 写成 1/len(...) 而不是三个 0.33：判据增删时其余各项自动等比缩放，从结构上杜绝这次
# 修的这类"项数变了、系数忘了改"。
LOAD_JUDGEMENTS: tuple[Callable[[Mapping[str, Any]], float], ...] = (
    _cpu_saturation,
    _memory_saturation,
    _task_saturation,
)


def calculate_load_score(metrics: Mapping[str, Any] | None) -> float:
    """越低越优。取不到指标时返回满分——不可知按最坏算，不许猜一个好看的数。"""
    if not metrics:
        return PERCENT_FULL
    total = sum(judgement(metrics) for judgement in LOAD_JUDGEMENTS)
    return round(total / len(LOAD_JUDGEMENTS), _SCORE_DIGITS)


__all__ = ["LOAD_JUDGEMENTS", "PERCENT_FULL", "calculate_load_score"]
