"""进程树轮询间隔的定档策略。

``RLIMIT_DATA`` 不覆盖 MAP_SHARED 匿名映射，这部分只能靠 ``_monitor_resources`` 的
轮询式 RSS 兜底，于是超限窗口 ≈ 采样间隔 × 弄脏页速率，故 间隔 = 限额 × 超支预算比 /
弄脏页速率，把超限倍数钉在 ~1.5×。（``write()`` 写进 tmpfs 的页不计 RLIMIT_DATA 也
**不进 RSS**，轮询看不见，由沙箱 tmpfs 的 ``--size`` 硬限，见 ``sandbox_scratch``。）

不按任务的 ``timeout_seconds`` 分档（旧实现如此）：那是任务作者可填的普通字段，等于让
任务自己选窗口大小。真机实测同一快速分配用例、限额都是 512MB，timeout=300 被杀在
1924MB（3.8×），timeout=3600 被杀在 3020MB（5.9×），后者把 3GiB 容器打到 99.95%。

不按"观测到的增长速率"自适应：最坏情况恰恰是一上来就猛冲的突发分配器，那时还没有历史
可用；而有了历史反馈环，慢热几秒再突刺就能把间隔拖长。

边界：轮询给不出**硬**上界，下面的速率常数是实测值不是证明。每任务的内核级 RSS 硬限额
在当前安全画像下做不到（见 ``process_limits.py``），容器级 ``mem_limit`` 才是最终兜底。
"""

from __future__ import annotations

# 实测弄脏页速率（2 CPU 配额的 Worker 容器内）：MAP_SHARED 匿名映射 4MiB 分块写入
# 1733 MB/s，页错误主导的路径 1536MB/0.838s ≈ 1830 MB/s。取 2048 留余量——常数只出现在
# 分母，取大只会把间隔调得更密，方向是安全的。
_OBSERVED_PEAK_DIRTY_RATE_MB_PER_SECOND = 2048.0

# 允许的超支幅度：限额的 50%，即把超限倍数压在 ~1.5× 以内。
_OVERSHOOT_BUDGET_RATIO = 0.5

# 采样下限：``sample_process_tree`` 实测单次 0.5ms（容器内），0.1s 间隔下每任务约占
# 单核 0.5%，满并发 4 路约 2%。再密下去收益递减而开销线性上升。
_MIN_INTERVAL_SECONDS = 0.1

# 采样上限：CPU 时间上限也走这个循环，且窗口绝对值（MB）不该无限膨胀。
_MAX_INTERVAL_SECONDS = 1.0


def resource_monitor_interval(memory_limit_mb: int) -> float:
    """按内存限额算出本次执行的采样间隔（秒）。

    ``memory_limit_mb <= 0`` 表示没配内存上限，此时监控只为 CPU 时间上限服务，按最粗档
    即可——调用方（``_start_resource_monitor``）已保证至少有一项上限存在。
    """
    if memory_limit_mb <= 0:
        return _MAX_INTERVAL_SECONDS
    ideal = memory_limit_mb * _OVERSHOOT_BUDGET_RATIO / _OBSERVED_PEAK_DIRTY_RATE_MB_PER_SECOND
    return min(_MAX_INTERVAL_SECONDS, max(_MIN_INTERVAL_SECONDS, ideal))


__all__ = ["resource_monitor_interval"]
