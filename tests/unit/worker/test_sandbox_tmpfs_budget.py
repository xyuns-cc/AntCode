"""沙箱内存盘（/tmp、/dev/shm）的尺寸必须来自任务自己的内存限额。

钉死的缺陷（真机复现，192.168.1.250 / antcode-worker，限额 1433MB）：
``--tmpfs`` 不带 ``--size`` 时内核按**宿主内存的一半**建 tmpfs——任务里 ``statvfs``
两处各报 16046MB（宿主 32GB），而容器 ``memory.max`` 只有 8192MB。tmpfs 页计入容器
memory cgroup 却**不进任何进程的 RSS**（``write()`` 写下去的页没有映射），于是
``sample_process_tree`` 完全看不见：dd 往 /tmp 写 3000MB（2.09×限额），任务树 RSS
全程 ≤6.1MB、exit 0 上报 SUCCESS，容器 memory.current 冲到 2816MB。

边界（必须知情）：本文件都是**参数构造与纯函数**级别的用例，只能证明"命令行怎么拼"
与"归因怎么算"。tmpfs 到底有没有被内核限住、写满时是不是 ENOSPC，只有真机能证明，
见提交说明里的实测记录。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from typing import Any, cast
from unittest.mock import MagicMock

import psutil
import pytest
from antcode_worker.domain.enums import ExitReason, RunStatus
from antcode_worker.domain.models import ExecPlan
from antcode_worker.executor import process as process_mod
from antcode_worker.executor.base import ExecutorConfig
from antcode_worker.executor.resource_sampler import ProcessTreeUsage, sample_process_tree
from antcode_worker.executor.sandbox_config import SandboxConfig
from antcode_worker.executor.sandbox_mounts import SandboxFilesystemRequest, sandbox_filesystem_args
from antcode_worker.executor.sandbox_plan import SandboxPlanRequest, _sandbox_context
from antcode_worker.executor.sandbox_provider import BasicSandbox
from antcode_worker.runtime import dependency_process

_TASK_MEMORY_LIMIT_MB = 1433
_PLAN_MEMORY_LIMIT_MB = 777
_BYTES_PER_MIB = 1024 * 1024
_SCRATCH_MOUNTS = ("/dev/shm", "/tmp")
_SCRATCH_PEAK_MB = 1432.5
# 真机臂的尺寸：小到跑得快，又要显著高于 sh/dd 自身几 MB 的 RSS 才有区分度
_REAL_CAP_MB = 64
_REAL_WRITE_MB = 32
_ALIVE_SECONDS = 10
_SAMPLE_POLL_SECONDS = 0.1


def _request(tmp_path, **overrides: Any) -> SandboxFilesystemRequest:
    work_dir = tmp_path / "runs" / "sources" / "run-1" / "project"
    work_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "runtimes").mkdir(exist_ok=True)
    fields: dict[str, Any] = {
        "work_dir": work_dir,
        "payload_executable": "/bin/sh",
        "data_root": tmp_path,
        "runtimes_root": tmp_path / "runtimes",
        "plugin_name": "code",
        "run_id": "run-1",
    }
    fields.update(overrides)
    return SandboxFilesystemRequest(**fields)


def _sized_mounts(args: tuple[str, ...]) -> dict[str, int]:
    """把 ``--size N --tmpfs DEST`` 收成 {挂载点: 字节数}。

    只认紧挨着的三元组——bwrap 的 ``--size`` 只作用于**紧随其后**的那一个 ``--tmpfs``，
    中间隔了别的参数就等于没生效，用例必须能把这种情况判红。
    """
    sized: dict[str, int] = {}
    for index, token in enumerate(args):
        if token == "--size" and args[index + 2 : index + 3] == ("--tmpfs",):
            sized[args[index + 3]] = int(args[index + 1])
    return sized


def test_both_scratch_mounts_are_sized_from_the_task_memory_limit(tmp_path) -> None:
    """/tmp 与 /dev/shm 都必须带 --size，且取本任务的内存限额。

    只限住 /tmp 是没用的：/dev/shm 同样是 tmpfs、同样计入容器内存，真机实测往它
    write() 600MB 一样畅通无阻，尺寸也一样报 16046MB。
    """
    args = sandbox_filesystem_args(_request(tmp_path, tmpfs_size_mb=_TASK_MEMORY_LIMIT_MB))

    assert _sized_mounts(args) == {mount: _TASK_MEMORY_LIMIT_MB * _BYTES_PER_MIB for mount in _SCRATCH_MOUNTS}


@pytest.mark.parametrize("size_mb", [0, -1])
def test_a_missing_task_memory_limit_is_refused_instead_of_falling_back_to_unsized(size_mb: int, tmp_path) -> None:
    """限额 <=0 不是"运维关掉了内存上限"，是接线断了，必须抛。

    ``init_worker_config`` 恒把 0 换成自适应或默认限额（下界 256MB），
    ``engine/config_update`` 走同一条区间校验，所以运行期没有合法路径能送来 0。
    旧实现在这里"0 就不下 --size"——一个断掉的接线于是安静地退回**宿主内存的一半**，
    防护不是被关掉，是被换成了这套代码正要消灭的那个值。
    """
    with pytest.raises(RuntimeError, match="不可知"):
        sandbox_filesystem_args(_request(tmp_path, tmpfs_size_mb=size_mb))


def test_wrap_command_refuses_a_context_without_an_explicit_scratch_size(tmp_path) -> None:
    """缺 tmpfs_size_mb 必须直接拒绝，不能默认成"不限"。

    默认放行等于让任何新增调用方悄悄拿回宿主内存一半的 tmpfs——本仓已经有两个
    wrap_command 调用方，第三个不该靠人记得加这个键。
    """
    work_dir = tmp_path / "runs" / "sources" / "run-1" / "project"
    work_dir.mkdir(parents=True)
    (tmp_path / "runtimes").mkdir()
    sandbox = BasicSandbox(
        MagicMock(
            sandbox_command=["/usr/bin/bwrap"],
            data_dir=str(tmp_path),
            runtimes_dir=str(tmp_path / "runtimes"),
            network_isolated=True,
        )
    )

    with pytest.raises(RuntimeError, match="tmpfs_size_mb"):
        sandbox.wrap_command(
            ["/bin/sh"],
            {"work_dir": str(work_dir), "plugin_name": "code", "run_id": "run-1"},
        )


@pytest.mark.parametrize(
    ("plan_limit_mb", "default_limit_mb", "expected_mb"),
    [
        (_PLAN_MEMORY_LIMIT_MB, _TASK_MEMORY_LIMIT_MB, _PLAN_MEMORY_LIMIT_MB),
        (0, _TASK_MEMORY_LIMIT_MB, _TASK_MEMORY_LIMIT_MB),
        (0, 0, 0),
    ],
)
def test_sandbox_context_carries_the_same_limit_the_rlimit_layer_uses(
    plan_limit_mb: int,
    default_limit_mb: int,
    expected_mb: int,
) -> None:
    """内存盘尺寸与 RLIMIT_DATA/RSS 监控必须是同一个生效限额。

    两层各写一遍 ``plan or default`` 就会分叉成"rlimit 按 A 收、tmpfs 按 B 切"，
    而这种分叉在真机上表现为"限额看起来生效了却拦不住"。
    """
    plan = ExecPlan(command="python", run_id="run-1", memory_limit_mb=plan_limit_mb)
    runtime = MagicMock(path="/data/worker/runtimes/h", python_executable="/usr/bin/python3", runtime_hash="h")
    config = ExecutorConfig(default_memory_limit_mb=default_limit_mb, default_max_processes=0)

    context = _sandbox_context(config, SandboxPlanRequest(exec_plan=plan, runtime_handle=runtime, context={}))

    assert context["tmpfs_size_mb"] == expected_mb


def test_dependency_preparation_sizes_its_scratch_from_its_own_memory_limit(monkeypatch, tmp_path) -> None:
    """依赖准备走的是同一套 bwrap 画像，同样的盲区，必须同样定尺寸。

    它的 RSS 监控（_resource_violation）也只看 memory_info().rss，漏 tmpfs 的方式一模一样。
    """
    monkeypatch.setattr(dependency_process, "_resolve_bwrap_command", lambda: ["/usr/bin/bwrap"])
    monkeypatch.setattr(
        "antcode_worker.executor.sandbox_provider.shutil.which",
        lambda name: sys.executable if name == "npm" else "/usr/bin/prlimit",
    )
    work_dir = tmp_path / "runs" / "sources" / "run-1" / "project"
    work_dir.mkdir(parents=True)
    (tmp_path / "runtimes").mkdir()

    wrapped = dependency_process._wrap_offline_command(
        ["npm", "ci"],
        work_dir,
        run_id="run-1",
        memory_mb=dependency_process.DEPENDENCY_MEMORY_MB,
    )

    expected = dependency_process.DEPENDENCY_MEMORY_MB * _BYTES_PER_MIB
    assert _sized_mounts(tuple(wrapped)) == {mount: expected for mount in _SCRATCH_MOUNTS}


def _process_info(*, exhausted: bool, peak_mb: float = _SCRATCH_PEAK_MB) -> process_mod.ProcessInfo:
    info = process_mod.ProcessInfo(
        process=cast(Any, None),
        run_id="run-1",
        started_at=datetime.now(),
        exec_plan=ExecPlan(command="/bin/sh", memory_limit_mb=_TASK_MEMORY_LIMIT_MB),
    )
    info.scratch_exhausted_at_last_sample = exhausted
    info.scratch_peak_mb = peak_mb
    return info


def test_filling_the_scratch_mount_is_reported_as_a_memory_budget_not_an_io_error() -> None:
    """写满内存盘时任务只会看到 ENOSPC，那是个像磁盘故障的 IO 错误。

    /tmp 与 /dev/shm 都是内存、计入容器内存额度；不翻译就等于把一条本来说得清的
    资源上限退化成一句费解的报错——这正是加 --size 时最容易付出的代价。

    只覆盖"已观测到写满"这一支。写满后立刻退出的任务采不到，归因退回裸退出码，
    那是轮询的固有边界，见 ``failure_message`` 文档串。
    """
    executor = process_mod.ProcessExecutor()
    status, reason, message = executor._determine_result(1, _process_info(exhausted=True))

    assert (status, reason) == (RunStatus.FAILED, ExitReason.ERROR)
    assert message is not None
    assert "退出码: 1" in message, "原始退出码不能被抹掉"
    assert str(_TASK_MEMORY_LIMIT_MB) in message, "必须说清撞的是哪个数"
    assert "内存" in message and "ENOSPC" in message, "必须点明它是内存额度而不是磁盘"


def test_a_normal_failure_is_not_relabelled_as_a_scratch_exhaustion() -> None:
    """反向臂：没观测到写满就不许套这套说辞，否则等于"什么错都报成内存盘满"。"""
    executor = process_mod.ProcessExecutor()
    _status, _reason, message = executor._determine_result(1, _process_info(exhausted=False))

    assert message == "退出码: 1"


class _FinishesAfterSamples:
    """跑满 ``samples`` 轮监控循环后退出。"""

    def __init__(self, samples: int = 1) -> None:
        self.pid = os.getpid()
        self._samples = samples
        self._polls = 0

    @property
    def returncode(self) -> int | None:
        self._polls += 1
        return None if self._polls <= self._samples else 0


def _usage(*, exhausted: bool, used_mb: float) -> ProcessTreeUsage:
    return ProcessTreeUsage(
        cpu_time_seconds=0.0,
        memory_rss_bytes=0,
        process_count=1,
        scratch_used_bytes=int(used_mb * _BYTES_PER_MIB),
        scratch_exhausted=exhausted,
    )


@pytest.mark.asyncio
async def test_monitor_records_what_the_sampler_saw_on_the_scratch_mounts(monkeypatch) -> None:
    """采样值必须真的落到 ProcessInfo 上，否则归因永远拿不到数。

    这条补的是"采样器会算 → 监控循环会记"之间的接线：只测采样器和只测归因函数
    都不会发现这段断掉。
    """
    usage = _usage(exhausted=True, used_mb=_SCRATCH_PEAK_MB)
    monkeypatch.setattr(process_mod, "sample_process_tree", lambda _root: usage)
    info = _process_info(exhausted=False, peak_mb=0)
    info.process = cast(Any, _FinishesAfterSamples())

    await process_mod.ProcessExecutor()._monitor_resources(info)

    assert info.scratch_exhausted_at_last_sample is True
    assert info.scratch_peak_mb == pytest.approx(_SCRATCH_PEAK_MB)


@pytest.mark.asyncio
async def test_a_scratch_mount_that_was_filled_and_then_freed_is_not_blamed_for_the_exit(monkeypatch) -> None:
    """写满 → 释放 → 因无关原因退出，归因**不该**再说"写满了内存盘"。

    旧实现把"是否写满"与峰值一样 latch 成 ``已有 or 本次``，于是任何一次瞬时写满都会
    永久改写这次执行的失败原因——运维照着"内存盘写满"去查内存，而真因在 stderr 里。
    峰值该 latch（它回答"最多用到多少"），写满不该（它回答"这次退出跟盘满有没有关系"）。
    """
    samples = iter([_usage(exhausted=True, used_mb=_SCRATCH_PEAK_MB), _usage(exhausted=False, used_mb=0.0)])
    monkeypatch.setattr(process_mod, "sample_process_tree", lambda _root: next(samples))
    info = _process_info(exhausted=False, peak_mb=0)
    info.process = cast(Any, _FinishesAfterSamples(samples=2))

    await process_mod.ProcessExecutor()._monitor_resources(info)

    assert info.scratch_exhausted_at_last_sample is False, "退出时盘是空的，不能贴写满的归因"
    # 峰值仍要保留：它是历史量，用来说明"最多用到多少"，与归因无关
    assert info.scratch_peak_mb == pytest.approx(_SCRATCH_PEAK_MB)
    _status, _reason, message = process_mod.ProcessExecutor()._determine_result(1, info)
    assert message == "退出码: 1"
