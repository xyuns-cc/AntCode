"""把子进程的采样值与退出码翻译成任务结果。

从 ``process.py`` 拆出，让后者只保留子进程生命周期编排。这里全是纯函数与一个数据
载体：判越线、判归因，不碰进程也不做 IO。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime

from antcode_worker.domain.enums import ExitReason, RunStatus
from antcode_worker.domain.models import ExecPlan
from antcode_worker.executor.resource_sampler import ProcessTreeUsage


@dataclass
class ProcessInfo:
    """进程信息"""

    process: asyncio.subprocess.Process
    run_id: str
    started_at: datetime
    exec_plan: ExecPlan
    cancelled: bool = False

    # 资源用量：采样写入，只供 describe_limit_breach / limit_breach_result 判越线。
    # proto TaskStatus 没有对应字段，数字只以 ExitReason.CPU_LIMIT/OOM 与错误文案离开
    # Worker——别再往 ExecResult 上复制一份，那份拷贝没有消费者。
    cpu_time_seconds: float = 0
    memory_peak_mb: float = 0

    # 沙箱内存盘（/tmp、/dev/shm）的用量峰值与"最后一次采样时是不是满的"。不参与主动
    # kill：写满由 tmpfs 的 --size 硬拦、内核直接给 ENOSPC，这两个字段只负责把那句
    # "No space left on device" 翻回"撞的是本任务的内存额度"。
    #
    # 峰值取 max、写满**不取** max：两者要回答的问题不同。峰值回答"最多用到多少"，
    # 是历史量；而归因回答的是"这次退出跟盘满有没有关系"，是退出时刻的状态量。
    # 旧实现把 exhausted 也 latch 成 `已有 or 本次`，于是"写满 → 释放 → 因无关原因
    # 退出"照样被贴上"任务写满了沙箱内存盘"——一条会把运维带去查错方向的误报。
    scratch_peak_mb: float = 0
    scratch_exhausted_at_last_sample: bool = False


def describe_limit_breach(usage: ProcessTreeUsage, exec_plan: ExecPlan) -> str | None:
    """返回进程树超限描述；未超限返回 None。"""
    cpu_limit = exec_plan.cpu_limit_seconds
    if cpu_limit > 0 and usage.cpu_time_seconds > cpu_limit:
        return f"进程树 CPU 时间超限: {usage.cpu_time_seconds:.1f}s > {cpu_limit}s"
    memory_limit_mb = exec_plan.memory_limit_mb
    if memory_limit_mb > 0 and usage.memory_rss_mb > memory_limit_mb:
        return f"进程树内存超限: {usage.memory_rss_mb:.1f}MB > {memory_limit_mb}MB"
    return None


def limit_breach_result(process_info: ProcessInfo) -> tuple[RunStatus, ExitReason, str] | None:
    """已采样的峰值是否越过 CPU/内存上限；未越过返回 None。"""
    exec_plan = process_info.exec_plan
    if exec_plan.cpu_limit_seconds > 0 and process_info.cpu_time_seconds > exec_plan.cpu_limit_seconds:
        return RunStatus.FAILED, ExitReason.CPU_LIMIT, "CPU 时间超限"
    if exec_plan.memory_limit_mb > 0 and process_info.memory_peak_mb > exec_plan.memory_limit_mb:
        return RunStatus.FAILED, ExitReason.OOM, "内存超限"
    return None


def failure_message(exit_code: int, process_info: ProcessInfo, scratch_limit_mb: int) -> str:
    """非零退出的错误文案；**最后一次采样仍是满的**时候才补上归因。

    内存盘写满，任务侧只会看到 "No space left on device"——一个像磁盘故障的 IO 错误，
    而 /tmp 与 /dev/shm 都是内存、计入容器内存额度。不翻译就等于把一条本来说得清的
    资源上限退化成一句费解的报错，那正是给 tmpfs 加 ``--size`` 最容易付出的代价。

    为什么看"最后一次采样"而不是"是否曾经满过"：满过并不蕴含"这次退出跟它有关"。
    写满 → 删掉临时文件 → 因编译报错/断言失败退出，是完全正常的任务形态；按"曾经满过"
    归因会给这类退出贴上"任务写满了沙箱内存盘"，运维照着这条去查内存，而真正的原因在
    stderr 里。进程树退出后它的挂载命名空间随即消失（``/proc/<pid>/root`` 不复存在），
    拿不到真正的"退出瞬间"状态，最后一次采样是可得的最近似值。

    能力边界（别把它当成保证），两侧都只是轮询精度：
    - 漏报：写满后**不足一个采样间隔**就退出的任务采不到满状态。真机实测（限额
      1433MB、间隔 0.35s）dd 以 3.2GB/s 写满即退，最后一次采样停在 ~1000MB，文案退回
      "退出码: N"。
    - 误报窗口：写满后在最后一次采样**之后**才释放并退出，仍会带上归因；窗口 ≤ 一个
      采样间隔，而不再是整个任务生命周期。
    两种情况下任务自己的 stderr 都带着那句 ENOSPC，只是没有"它是内存、上限多少"这层
    解释。与 ``monitor_interval`` 里那条一样：轮询给不出硬保证，拦截由 ``--size`` 兑现，
    这里只负责解释。
    """
    base = f"退出码: {exit_code}"
    if not process_info.scratch_exhausted_at_last_sample:
        return base
    return (
        f"{base}；任务写满了沙箱内存盘（峰值 {process_info.scratch_peak_mb:.1f}MB，"
        f"/tmp 与 /dev/shm 各自上限 {scratch_limit_mb}MB，取自本任务的内存限额）。"
        f"它们是内存不是磁盘，计入容器内存额度，写满时内核对写入返回 "
        f"ENOSPC(No space left on device)"
    )


__all__ = [
    "ProcessInfo",
    "describe_limit_breach",
    "failure_message",
    "limit_breach_result",
]
