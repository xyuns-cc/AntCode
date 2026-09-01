"""把子进程的采样值与退出码翻译成任务结果：判越线、判归因，不碰进程也不做 IO。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime

from antcode_worker.domain.enums import ExitReason, RunStatus
from antcode_worker.domain.models import ExecPlan
from antcode_worker.executor.resource_sampler import ProcessTreeUsage


@dataclass
class ProcessInfo:
    process: asyncio.subprocess.Process
    run_id: str
    started_at: datetime
    exec_plan: ExecPlan
    cancelled: bool = False

    # 只供 describe_limit_breach / limit_breach_result 判越线。proto TaskStatus 没有对应
    # 字段，数字只以 ExitReason.CPU_LIMIT/OOM 与错误文案离开 Worker——别再往 ExecResult
    # 上复制一份，那份拷贝没有消费者。
    cpu_time_seconds: float = 0
    memory_peak_mb: float = 0

    # 沙箱内存盘（/tmp、/dev/shm）用量。不参与主动 kill：写满由 tmpfs 的 --size 硬拦，
    # 这两个字段只负责把 "No space left on device" 翻回"撞的是本任务的内存额度"。
    #
    # 峰值取 max、写满**不取** max：峰值是历史量，而归因问的是"这次退出跟盘满有没有
    # 关系"，是退出时刻的状态量。把 exhausted 也 latch 成 `已有 or 本次`，会让"写满 →
    # 释放 → 因无关原因退出"被误报成写满内存盘，把运维带去查错方向。
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

    看"最后一次采样"而不是"是否曾经满过"：写满 → 删临时文件 → 因编译报错退出是正常
    任务形态，按"曾经满过"归因会把这类退出误标成写满内存盘。进程树退出后挂载命名空间
    随即消失（``/proc/<pid>/root`` 不复存在），最后一次采样是可得的最近似值。

    能力边界（两侧都只是轮询精度，不是保证）：
    - 漏报：写满后不足一个采样间隔就退出的采不到。实测（限额 1433MB、间隔 0.35s）dd 以
      3.2GB/s 写满即退，最后一次采样停在 ~1000MB，文案退回"退出码: N"。
    - 误报：写满后在最后一次采样之后才释放并退出仍会带归因，窗口 ≤ 一个采样间隔。

    两种情况任务 stderr 都带着 ENOSPC，只是少了"它是内存、上限多少"这层解释。拦截由
    ``--size`` 兑现，这里只负责解释。
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
