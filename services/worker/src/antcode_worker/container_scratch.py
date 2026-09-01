"""容器自己那几个内存盘的尺寸，必须来自容器坐标系而不是宿主。

Docker 的 ``tmpfs:`` 声明不带 ``size=`` 时，内核按**宿主内存的一半**建盘——不是按
本容器的 ``mem_limit``。真机实测（宿主 32GB）：``mem_limit`` 分别为 4g 与 3g 的两个
Worker 容器里，``/tmp`` 与 ``/home/appuser/.cache`` 各报 16047MB，是容器全部额度的
4~5 倍。

这与沙箱层拿不到 ``--size`` 的那两个 tmpfs（``/`` 与 ``/dev``，见
``executor/sandbox_scratch.py``）、JVM 读不到 cgroup 按宿主内存定堆尺寸（见
``runtime/runtime_budget.py``）、自适应限额读宿主 /proc（见 ``resource_budget.py``）
是同一族缺陷：**值从错误的坐标系算出来**。

为什么校验要落在 Worker 里，而不是只改 compose：尺寸是容器创建期属性，进程无法自己
改；而 compose 文件不止仓库里这几份（真机长期跑的 ``docker-compose.mn.yml`` 就不在
HEAD 里）。只改仓库里的声明，等于"修好的那份修好了"，任何一份没跟上的声明仍会静默地
把宿主的一半带回来。这里做的是**判据**：一个 tmpfs 的尺寸如果超过整个容器的内存额度，
它就不可能是从容器坐标系导出来的。

只在确实有容器内存额度时判：没有 cgroup 上限就是裸机部署，宿主总量本来就是正确答案
（与 ``resolve_memory_budget`` 同一条语义）。
"""

from __future__ import annotations

import os
from pathlib import Path

from antcode_worker.resource_budget import BudgetSource, MemoryBudget, ResourceBudgetError

# 挂载表：本进程 mount namespace 里的最终形态，容器内读到的就是本容器的挂载。
PROC_SELF_MOUNTS = Path("/proc/self/mounts")
_TMPFS_TYPE = "tmpfs"
_MOUNT_POINT_FIELD = 1
_FS_TYPE_FIELD = 2
_MIN_MOUNT_FIELDS = 3
_BYTES_PER_MIB = 1024 * 1024


def _tmpfs_mount_points(raw: str) -> tuple[str, ...]:
    """从 ``/proc/self/mounts`` 里挑出 tmpfs 挂载点。

    只认 fstype 为 ``tmpfs`` 的行：普通磁盘挂载的容量与内存额度没有可比性，拿它们
    去比会把裸机上一块大盘上的 /tmp 判成缺陷。挂载点里的空格等字符在 procfs 里是
    八进制转义（``\\040``），解回来才能在错误信息里对得上运维看到的路径。
    """
    points = []
    for line in raw.splitlines():
        fields = line.split()
        if len(fields) < _MIN_MOUNT_FIELDS or fields[_FS_TYPE_FIELD] != _TMPFS_TYPE:
            continue
        points.append(fields[_MOUNT_POINT_FIELD].encode().decode("unicode_escape"))
    return tuple(points)


def _mount_total_bytes(mount_point: str) -> int | None:
    """挂载点的总容量；读不到返回 None。

    挂载表是快照，容器停机流程里挂载可能刚好消失；那是竞态不是缺陷。这与"尺寸超了"
    必须区分：后者要拒绝启动，前者只是这一项没得判。
    """
    try:
        stat = os.statvfs(mount_point)
    except OSError:
        return None
    return stat.f_blocks * stat.f_frsize


def oversized_scratch_mounts(budget: MemoryBudget) -> tuple[tuple[str, int], ...]:
    """返回尺寸超过整个容器内存额度的 tmpfs 挂载点及其字节数。"""
    if not PROC_SELF_MOUNTS.exists():
        return ()
    raw = PROC_SELF_MOUNTS.read_text(encoding="utf-8")
    oversized = []
    for mount_point in _tmpfs_mount_points(raw):
        total_bytes = _mount_total_bytes(mount_point)
        if total_bytes is not None and total_bytes > budget.total_bytes:
            oversized.append((mount_point, total_bytes))
    return tuple(oversized)


def validate_container_scratch_fits_budget(budget: MemoryBudget) -> None:
    """容器内存盘尺寸放不进容器内存额度时拒绝启动。

    判据只有一条、且是充分的：tmpfs 页计入本容器的 memory cgroup，所以一个尺寸大于
    整个容器额度的内存盘，其尺寸不可能是从容器坐标系导出来的——只能是内核按宿主内存
    填的默认值。反过来，任何按容器额度（或其一部分）声明的尺寸都不会触发这条。

    为什么是抛而不是告警：进程改不了自己容器的 tmpfs 尺寸，没有"按预算重算"这个选项
    （``fit_task_memory_to_budget`` 之所以能降级告警，是因为限额那个数它自己算得出来）。
    剩下的两条路只有"拒绝启动"和"带着一个宿主来源的内存盘照跑"，后者正是本模块要
    消灭的静默失败。
    """
    if budget.source is BudgetSource.HOST:
        return
    oversized = oversized_scratch_mounts(budget)
    if not oversized:
        return
    detail = "、".join(f"{point}={total // _BYTES_PER_MIB}MB" for point, total in oversized)
    raise ResourceBudgetError(
        f"容器内存盘尺寸超过整个容器的内存额度: {detail}（预算 {budget.describe()}）。"
        "tmpfs 页计入本容器的 memory cgroup，这个尺寸不可能是从本容器的坐标系导出来的。"
        "两种成因：compose 的 tmpfs 声明漏了 size=（内核按宿主内存的一半填），"
        "或 size= 抄自另一个容器的 mem_limit（compose extends 会把整份 tmpfs 列表继承过来，"
        "而 mem_limit 是各服务各写的）。请把 size= 改成**本容器**的 mem_limit"
        "（见 infra/docker/docker-compose.prod.worker.yml）。"
    )


__all__ = [
    "PROC_SELF_MOUNTS",
    "oversized_scratch_mounts",
    "validate_container_scratch_fits_budget",
]
