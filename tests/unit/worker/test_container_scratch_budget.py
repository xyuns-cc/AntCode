"""容器自己的内存盘尺寸必须来自容器坐标系，不能是宿主内存的一半。

钉死的缺陷（真机复现，192.168.1.250）：Docker 的 ``tmpfs:`` 声明不带 ``size=`` 时内核
按**宿主内存的一半**建盘。宿主 32GB 上，``mem_limit`` 分别为 4g 与 3g 的两个 Worker
容器里 ``/tmp`` 与 ``/home/appuser/.cache`` 各报 16047MB——容器全部额度的 4~5 倍。
``cd8e7ad`` 只收了沙箱层（bwrap ``--tmpfs``），容器层是同一个洞。

边界（必须知情）：这里是纯函数级用例，只能证明"判据怎么算"。挂载表长什么样、内核
是否真按 size 建盘，只有真机能证明。
"""

from __future__ import annotations

import pytest
from antcode_worker import container_scratch
from antcode_worker.resource_budget import BudgetSource, MemoryBudget, ResourceBudgetError

_BYTES_PER_MIB = 1024 * 1024
# 真机数字：宿主 32GB 的一半 vs 容器 mem_limit=4g。
_HOST_HALF_MB = 16047
_CONTAINER_LIMIT_MB = 4096
_MOUNTS_WITH_HOST_SIZED_TMP = """\
overlay / overlay rw,relatime 0 0
proc /proc proc rw,nosuid,nodev,noexec,relatime 0 0
tmpfs /tmp tmpfs rw,nosuid,nodev,exec,relatime,size=16432128k,uid=1000,gid=1000 0 0
shm /dev/shm tmpfs rw,nosuid,nodev,noexec,relatime,size=1048576k 0 0
/dev/sda1 /app/data ext4 rw,relatime 0 0
"""


def _budget(source: BudgetSource, total_mb: int = _CONTAINER_LIMIT_MB) -> MemoryBudget:
    return MemoryBudget(total_bytes=total_mb * _BYTES_PER_MIB, source=source, origin="test")


def test_only_tmpfs_lines_are_measured() -> None:
    """只有内存盘的容量与内存额度可比。

    把普通磁盘挂载一起算进去，会把裸机上一块大盘上的 /tmp 判成缺陷——那是把这条判据
    从"尺寸来源错了"偷换成"盘比内存大"，两回事。
    """
    assert container_scratch._tmpfs_mount_points(_MOUNTS_WITH_HOST_SIZED_TMP) == ("/tmp", "/dev/shm")


@pytest.mark.parametrize("scratch_mb", [_HOST_HALF_MB, _CONTAINER_LIMIT_MB + 1])
def test_a_scratch_mount_larger_than_the_whole_container_is_refused(scratch_mb, monkeypatch, tmp_path) -> None:
    """内存盘尺寸大于整个容器额度 ⇒ 它不可能是从容器坐标系导出来的，必须拒绝启动。

    tmpfs 页计入本容器的 memory cgroup，所以"比整个容器还大"不可能是按本容器算出来的。
    两个参数分别对应两种真实成因：漏 size=（内核按宿主内存的一半填），以及 size= 抄自
    另一个容器的 mem_limit——真机上 compose ``extends`` 就把 dev worker（4g）的整份
    tmpfs 列表继承给了 mem_limit=3g 的 mn worker2，判据必须同时抓到这一种。

    进程改不了自己容器的 tmpfs 尺寸，没有"按预算重算"这个降级选项——只剩"拒绝启动"
    和"带着一个与容器额度无关的内存盘照跑"，后者正是要消灭的静默失败。
    """
    _patch_mounts(monkeypatch, tmp_path, {"/tmp": scratch_mb, "/dev/shm": 1024})

    with pytest.raises(ResourceBudgetError, match="mem_limit"):
        container_scratch.validate_container_scratch_fits_budget(_budget(BudgetSource.CGROUP_V2))


def test_a_scratch_mount_sized_from_the_container_limit_is_accepted(monkeypatch, tmp_path) -> None:
    """反向臂：按 mem_limit 声明的尺寸不能被判红，否则这条校验会把正常部署挡在门外。"""
    _patch_mounts(monkeypatch, tmp_path, {"/tmp": _CONTAINER_LIMIT_MB, "/dev/shm": 1024})

    container_scratch.validate_container_scratch_fits_budget(_budget(BudgetSource.CGROUP_V2))


def test_a_host_sourced_budget_skips_the_check(monkeypatch, tmp_path) -> None:
    """没有 cgroup 上限就是裸机部署，宿主总量本来就是正确答案。

    与 ``resolve_memory_budget`` 同一条语义：那里"读不到 cgroup 上限 → 宿主总量是对的"，
    这里就不能反过来拿宿主总量当缺陷证据。不豁免的话，任何裸机 Worker 都起不来。
    """
    _patch_mounts(monkeypatch, tmp_path, {"/tmp": _HOST_HALF_MB})

    container_scratch.validate_container_scratch_fits_budget(_budget(BudgetSource.HOST))


def _patch_mounts(monkeypatch, tmp_path, sizes: dict[str, int]) -> None:
    """伪造挂载表与 statvfs：容器挂载在测试进程里不存在，只能替掉这两个读取点。"""
    lines = "".join(f"tmpfs {point} tmpfs rw 0 0\n" for point in sizes)
    mounts = tmp_path / "mounts"
    mounts.write_text(lines, encoding="utf-8")
    monkeypatch.setattr(container_scratch, "PROC_SELF_MOUNTS", mounts)
    monkeypatch.setattr(
        container_scratch,
        "_mount_total_bytes",
        lambda point: sizes[point] * _BYTES_PER_MIB,
    )
