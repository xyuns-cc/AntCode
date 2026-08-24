"""进程树资源采样。

B8: 启用沙箱后 ``asyncio.subprocess.Process.pid`` 指向的只是 ``bwrap`` 外壳进程
（rule 场景外面还多包一层 relay），真正跑用户代码的是它的子孙进程。若只读根进程的
``memory_info().rss``，结果永远只有几 MB，内存/CPU 超限判定因此永不成立、主动 kill
形同虚设。这里统一按整棵进程树求和。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import psutil

_BYTES_PER_MIB = 1024 * 1024

# 沙箱里的两个内存盘。它们的页计入容器 memory cgroup，却**不进任何进程的 RSS**
# （write() 写下去的页没有被映射），所以下面的 RSS 求和永远看不见它们。这里单独
# 采一份，只为在任务因写满而失败时能说清"撞的是哪条限额"——拦截由 tmpfs 的
# --size 负责，见 sandbox_mounts。
_SCRATCH_MOUNTS = ("/tmp", "/dev/shm")


@dataclass(frozen=True)
class _ScratchSample:
    """某一个内存盘挂载实例的用量（按 st_dev 去重，一个挂载只算一次）。"""

    device: int
    used_bytes: int
    exhausted: bool


@dataclass(frozen=True)
class ProcessTreeUsage:
    """一次采样得到的整棵进程树资源用量。"""

    cpu_time_seconds: float
    memory_rss_bytes: int
    process_count: int
    scratch_used_bytes: int = 0
    scratch_exhausted: bool = False

    @property
    def memory_rss_mb(self) -> float:
        return self.memory_rss_bytes / _BYTES_PER_MIB

    @property
    def scratch_used_mb(self) -> float:
        return self.scratch_used_bytes / _BYTES_PER_MIB


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
    scratch: dict[int, _ScratchSample] = {}
    own_devices = _worker_scratch_devices()
    for member in members:
        sample = _sample_member(member)
        if sample is None:
            continue
        member_cpu_seconds, member_rss_bytes = sample
        cpu_time_seconds += member_cpu_seconds
        memory_rss_bytes += member_rss_bytes
        process_count += 1
        scratch.update(_sample_scratch(member.pid, own_devices))

    if process_count == 0:
        return None
    return ProcessTreeUsage(
        cpu_time_seconds=cpu_time_seconds,
        memory_rss_bytes=memory_rss_bytes,
        process_count=process_count,
        scratch_used_bytes=sum(item.used_bytes for item in scratch.values()),
        scratch_exhausted=any(item.exhausted for item in scratch.values()),
    )


def _worker_scratch_devices() -> frozenset[int]:
    """Worker 自己那几个内存盘的设备号，用来把它们排除在任务用量之外。"""
    devices = set()
    for mount in _SCRATCH_MOUNTS:
        try:
            devices.add(os.stat(mount).st_dev)
        except OSError:
            continue
    return frozenset(devices)


def _sample_scratch(pid: int, own_devices: frozenset[int]) -> dict[int, _ScratchSample]:
    """读某个进程所在挂载命名空间里的内存盘用量，按设备号去重。

    进程树的根是 bwrap 外壳，它留在 Worker 自己的挂载命名空间里，读到的是 Worker 的
    /tmp；必须按 st_dev 把它排除，否则会把 Worker 自己的临时文件算到任务头上。真机实测
    同一棵树：外壳 pid 读到 0.0MB，命名空间内的三个 pid 都读到 700.0MB。
    """
    found: dict[int, _ScratchSample] = {}
    for mount in _SCRATCH_MOUNTS:
        sample = _read_scratch_mount(f"/proc/{pid}/root{mount}", own_devices)
        if sample is not None:
            found[sample.device] = sample
    return found


def _read_scratch_mount(path: str, own_devices: frozenset[int]) -> _ScratchSample | None:
    """读不到就返回 None：进程随时会消失，未启用沙箱时也压根没有独立的内存盘。

    这与 ``_sample_member`` 的 AccessDenied 上抛不是一回事——RSS 采少了会让超限判定
    失效，而这里采不到只是少一条**归因**信息，拦截仍由 tmpfs --size 兑现。
    """
    try:
        device = os.stat(path).st_dev
        if device in own_devices:
            return None
        stat = os.statvfs(path)
    except OSError:
        return None
    used_bytes = (stat.f_blocks - stat.f_bfree) * stat.f_frsize
    return _ScratchSample(device=device, used_bytes=used_bytes, exhausted=stat.f_bavail == 0)


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
