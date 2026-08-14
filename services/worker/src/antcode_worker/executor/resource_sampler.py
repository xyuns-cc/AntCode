"""进程树资源采样。

B8: 启用沙箱后 ``asyncio.subprocess.Process.pid`` 指向的只是 ``bwrap`` 外壳进程
（rule 场景外面还多包一层 relay），真正跑用户代码的是它的子孙进程。若只读根进程的
``memory_info().rss``，结果永远只有几 MB，内存/CPU 超限判定因此永不成立、主动 kill
形同虚设。这里统一按整棵进程树求和。
"""

from __future__ import annotations

from dataclasses import dataclass

import psutil

_BYTES_PER_MIB = 1024 * 1024


@dataclass(frozen=True)
class ProcessTreeUsage:
    """一次采样得到的整棵进程树资源用量。"""

    cpu_time_seconds: float
    memory_rss_bytes: int
    process_count: int

    @property
    def memory_rss_mb(self) -> float:
        return self.memory_rss_bytes / _BYTES_PER_MIB


def sample_process_tree(root: psutil.Process) -> ProcessTreeUsage | None:
    """汇总 ``root`` 及其全部子孙进程的 CPU 时间与 RSS。

    返回 ``None`` 表示整棵树在采样时已经不存在（进程正常结束）。

    进程在遍历过程中消失是正常竞态（``ZombieProcess`` 是 ``NoSuchProcess`` 的子类），
    跳过该成员即可；其余异常（如 ``AccessDenied``）说明采样结果不可信，一律上抛，
    绝不返回一个偏小的用量把超限判定悄悄绕过去。
    """
    members = _collect_tree(root)
    if members is None:
        return None

    cpu_time_seconds = 0.0
    memory_rss_bytes = 0
    process_count = 0
    for member in members:
        sample = _sample_member(member)
        if sample is None:
            continue
        member_cpu_seconds, member_rss_bytes = sample
        cpu_time_seconds += member_cpu_seconds
        memory_rss_bytes += member_rss_bytes
        process_count += 1

    if process_count == 0:
        return None
    return ProcessTreeUsage(
        cpu_time_seconds=cpu_time_seconds,
        memory_rss_bytes=memory_rss_bytes,
        process_count=process_count,
    )


def _collect_tree(root: psutil.Process) -> list[psutil.Process] | None:
    try:
        return [root, *root.children(recursive=True)]
    except psutil.NoSuchProcess:
        return None


def _sample_member(member: psutil.Process) -> tuple[float, int] | None:
    try:
        cpu_times = member.cpu_times()
        return cpu_times.user + cpu_times.system, member.memory_info().rss
    except psutil.NoSuchProcess:
        return None


__all__ = ["ProcessTreeUsage", "sample_process_tree"]
