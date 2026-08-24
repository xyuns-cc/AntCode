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

    # 沙箱内存盘（/tmp、/dev/shm）的用量峰值与"是否观测到写满"。不参与主动 kill：
    # 写满由 tmpfs 的 --size 硬拦、内核直接给 ENOSPC，这两个字段只负责把那句
    # "No space left on device" 翻回"撞的是本任务的内存额度"。
    scratch_peak_mb: float = 0
    scratch_exhausted: bool = False


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
    """非零退出的错误文案；**观测到**内存盘写满时补上归因。

    内存盘写满，任务侧只会看到 "No space left on device"——一个像磁盘故障的 IO 错误，
    而 /tmp 与 /dev/shm 都是内存、计入容器内存额度。不翻译就等于把一条本来说得清的
    资源上限退化成一句费解的报错，那正是给 tmpfs 加 ``--size`` 最容易付出的代价。

    能力边界（别把它当成保证）：判据来自 ``_monitor_resources`` 的轮询采样，因此只在
    任务写满后**至少存活一个采样间隔**时才成立。真机实测（限额 1433MB、间隔 0.35s）：
    dd 以 3.2GB/s 写满后立刻退出的任务，最后一次采样停在 ~1000MB，归因不触发，文案退回
    "退出码: N"；写满后仍存活的任务稳定采到 1433MB/写满，归因触发。两种情况下任务自己的
    stderr 都带着那句 ENOSPC，只是没有"它是内存、上限多少"这层解释。
    与 ``monitor_interval`` 里那条一样：轮询给不出硬保证，拦截由 ``--size`` 兑现，
    这里只负责解释。
    """
    base = f"退出码: {exit_code}"
    if not process_info.scratch_exhausted:
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
