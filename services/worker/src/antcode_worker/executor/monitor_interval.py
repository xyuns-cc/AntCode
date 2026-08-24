"""进程树轮询间隔的定档策略。

``RLIMIT_DATA`` 不覆盖 MAP_SHARED 匿名映射，这部分只能靠 ``_monitor_resources``
的轮询式 RSS 兜底，于是"两次采样之间任务能多吃多少"就是真实的超限窗口：

（``write()`` 写进 tmpfs 的页同样不计入 RLIMIT_DATA，但它也**不进 RSS**，轮询看不见，
不在本模块的窗口讨论范围内——那条路径由沙箱 tmpfs 的 ``--size`` 硬限，见
``sandbox_mounts``。）

    超限窗口 ≈ 采样间隔 × 弄脏页速率

历史实现按**任务自己的 ``timeout_seconds``** 分档（≤60s→0.5s、≤300s→1.0s、
≤1800s→2.0s、>1800s→5.0s）。``timeout_seconds`` 是普通任务字段（``gt=0``、默认
3600、无上限），于是**窗口大小由任务作者自己选**：真机实测同一个快速分配用例，
限额都是 512MB，timeout=300 的被杀在 1924MB（3.8×），timeout=3600 的被杀在
3020MB（5.9×），后者把 3GiB 容器打到 99.95%。"任务允许跑多久"和"限额被突破得
多快"是两件无关的事，这个耦合没有道理。

改由**限额本身**定档：允许的超支是限额的一个固定比例，于是

    间隔 = 限额 × 超支预算比 / 弄脏页速率

超限倍数被钉在 ~1.5×，与限额大小、与任务时长都无关，且任务作者无法影响。

为什么不按"观测到的增长速率"自适应：最坏情况恰恰是一上来就猛冲的突发分配器，
那时监控还没有历史可用；而有了历史反馈环，慢热几秒再突刺就能把间隔拖长。
按限额定档是确定性的、可审计的，且没有任务可控输入。

必须写清的边界：轮询给不出**硬**上界。真实窗口还取决于实际弄脏速率能有多快，
下面这个速率常数是实测值不是证明。每任务的内核级 RSS 硬限额在当前安全画像下
做不到（见 ``process_limits.py``），容器级 ``mem_limit`` 才是最终兜底。
"""

from __future__ import annotations

# 实测弄脏页速率（测试机 192.168.1.250，2 CPU 配额的 Worker 容器内）：
# MAP_SHARED 匿名映射 4MiB 分块写入 1733 MB/s；上一轮终验在同一宿主上按页错误
# 主导的路径测到 1536MB/0.838s ≈ 1830 MB/s。取 2048 MB/s（向上取到 2 的幂）留余量
# ——这个常数只出现在分母，取大只会把间隔调得更密，方向是安全的。
_OBSERVED_PEAK_DIRTY_RATE_MB_PER_SECOND = 2048.0

# 允许的超支幅度：限额的 50%，即目标把超限倍数压在 ~1.5× 以内。
_OVERSHOOT_BUDGET_RATIO = 0.5

# 采样下限：``sample_process_tree`` 实测单次 0.5ms（容器内），0.1s 间隔下每个被
# 监控任务约占单核 0.5%，满并发 4 路约 2%。再密下去收益递减而开销线性上升。
_MIN_INTERVAL_SECONDS = 0.1

# 采样上限：限额很大时公式会给出 >1s 的间隔，但 CPU 时间上限也走这个循环，
# 且窗口的绝对值（MB）不该无限膨胀，统一封顶到 1s。
_MAX_INTERVAL_SECONDS = 1.0


def resource_monitor_interval(memory_limit_mb: int) -> float:
    """按内存限额算出本次执行的采样间隔（秒）。

    ``memory_limit_mb <= 0`` 表示没有配内存上限，这时监控只为 CPU 时间上限服务，
    按最粗档采样即可——调用方（``_start_resource_monitor``）已经保证至少有一项
    上限存在，不存在"没有任何上限却在轮询"的情况。
    """
    if memory_limit_mb <= 0:
        return _MAX_INTERVAL_SECONDS
    ideal = memory_limit_mb * _OVERSHOOT_BUDGET_RATIO / _OBSERVED_PEAK_DIRTY_RATE_MB_PER_SECOND
    return min(_MAX_INTERVAL_SECONDS, max(_MIN_INTERVAL_SECONDS, ideal))


__all__ = ["resource_monitor_interval"]
