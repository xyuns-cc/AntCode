"""B8: 资源监控必须统计整棵进程树，而不是只看根进程。

启用沙箱时 ``process.pid`` 只是 bwrap 外壳（rule 场景外面还有一层 relay），
真正吃内存的是它的子孙进程。只看根进程的 RSS 会让超限判定永不成立，
CPU/内存上限形同虚设。

这里用真实的"外壳 + 吃内存孙进程"结构验证采样与主动 kill。
"""

from __future__ import annotations

import asyncio
import contextlib
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime

import psutil
import pytest
from antcode_worker.domain.models import ExecPlan
from antcode_worker.executor import process as process_mod
from antcode_worker.executor import process_limits
from antcode_worker.executor.resource_sampler import ProcessTreeUsage, sample_process_tree

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX only")

_BYTES_PER_MIB = 1024 * 1024

# 进程树用例：孙进程吃掉的内存必须显著高于解释器自身开销，才能区分"只看根进程"
_BALLOON_MIB = 128
_LIMIT_MARGIN_MIB = 48
_TREE_READY_TIMEOUT_SECONDS = 30
_MONITOR_TIMEOUT_SECONDS = 30
_TREE_SLEEP_SECONDS = 600
# 孙进程按页触碰内存，确保 RSS 真的被计入而不是只保留虚拟映射
_TOUCH_STRIDE_BYTES = 4096

# 真实进程树至少包含外壳与它的一个子孙
_MIN_TREE_MEMBERS = 2

# 假进程树：两个存活成员 + 一个遍历中消失的成员
_ALIVE_MEMBER_COUNT = 2
_FAKE_USER_CPU_SECONDS = 1.5
_FAKE_SYSTEM_CPU_SECONDS = 0.5
_FAKE_MEMBER_RSS_MIB = 8
_EXPECTED_TREE_CPU_SECONDS = (_FAKE_USER_CPU_SECONDS + _FAKE_SYSTEM_CPU_SECONDS) * _ALIVE_MEMBER_COUNT
_EXPECTED_TREE_RSS_MIB = float(_FAKE_MEMBER_RSS_MIB * _ALIVE_MEMBER_COUNT)

# 超限判定用例：上限落在"根进程单独用量"与"整棵树用量"之间才有区分度
_BREACH_LIMIT_MIB = 32
_BREACH_TREE_RSS_MIB = 64
_BREACH_TREE_MEMBERS = 3
_BREACH_ROOT_ONLY_RSS_MIB = 4

# 清理残留进程树的等待参数
_KILL_TREE_TIMEOUT_SECONDS = 10
_KILL_TREE_POLL_SECONDS = 0.05


def _exec_plan(*, memory_limit_mb: int = 0, timeout_seconds: int = 60) -> ExecPlan:
    return ExecPlan(
        command=sys.executable,
        run_id="run-1",
        memory_limit_mb=memory_limit_mb,
        timeout_seconds=timeout_seconds,
    )


def test_sample_process_tree_counts_descendants() -> None:
    """真实进程树：孙进程吃掉的内存必须被计入。

    模拟沙箱布局——根进程相当于 bwrap 外壳，几乎不占内存，真正吃内存的是它
    fork 出来的孙进程。只看根进程的 RSS 会漏掉绝大部分用量。
    """
    shell = _spawn_ballooning_tree()
    try:
        root = psutil.Process(shell.pid)
        usage = sample_process_tree(root)

        assert usage is not None
        assert usage.process_count >= _MIN_TREE_MEMBERS, "必须同时统计到外壳进程与它的子孙"
        root_only_mb = root.memory_info().rss / _BYTES_PER_MIB
        assert usage.memory_rss_mb > root_only_mb, "整棵树的 RSS 必须大于根进程单独的 RSS"
        assert usage.memory_rss_mb >= _BALLOON_MIB, (
            f"孙进程占用的 {_BALLOON_MIB}MB 必须被计入，实际只统计到 {usage.memory_rss_mb:.1f}MB"
        )
    finally:
        _kill_tree(shell.pid)
        shell.wait(timeout=_TREE_READY_TIMEOUT_SECONDS)


def test_sample_process_tree_returns_none_for_dead_root() -> None:
    """根进程已消失是正常情况，返回 None 而不是抛错。"""

    class _GoneRoot:
        def children(self, recursive: bool = False) -> list[object]:
            raise psutil.NoSuchProcess(pid=-1)

    assert sample_process_tree(_GoneRoot()) is None  # type: ignore[arg-type]
    # 反向覆盖：活着的进程必须采到数据
    assert sample_process_tree(psutil.Process()) is not None


def test_sample_process_tree_skips_members_that_vanish() -> None:
    """成员在遍历中消失属于正常竞态，跳过它并继续统计其余成员。"""

    class _Vanishing:
        def cpu_times(self) -> object:
            raise psutil.NoSuchProcess(pid=-1)

        def memory_info(self) -> object:
            raise psutil.NoSuchProcess(pid=-1)

    class _Alive:
        def cpu_times(self) -> object:
            return _CpuTimes(user=_FAKE_USER_CPU_SECONDS, system=_FAKE_SYSTEM_CPU_SECONDS)

        def memory_info(self) -> object:
            return _MemoryInfo(rss=_FAKE_MEMBER_RSS_MIB * _BYTES_PER_MIB)

    class _Root(_Alive):
        def children(self, recursive: bool = False) -> list[object]:
            return [_Vanishing(), _Alive()]

    usage = sample_process_tree(_Root())  # type: ignore[arg-type]

    assert usage is not None
    assert usage.process_count == _ALIVE_MEMBER_COUNT, "只应统计到两个仍存活的成员"
    assert usage.cpu_time_seconds == pytest.approx(_EXPECTED_TREE_CPU_SECONDS)
    assert usage.memory_rss_mb == pytest.approx(_EXPECTED_TREE_RSS_MIB)


def test_sample_process_tree_propagates_unexpected_errors() -> None:
    """AccessDenied 之类的异常必须上抛——静默少算等于悄悄放行超限进程。"""

    class _Denied:
        def children(self, recursive: bool = False) -> list[object]:
            raise psutil.AccessDenied(pid=-1)

    with pytest.raises(psutil.AccessDenied):
        sample_process_tree(_Denied())  # type: ignore[arg-type]


def test_limit_breach_uses_whole_tree_usage() -> None:
    """超限判定读的是整棵树的用量，而不是根进程那几 MB。"""
    plan = _exec_plan(memory_limit_mb=_BREACH_LIMIT_MIB)
    tree_usage = ProcessTreeUsage(
        cpu_time_seconds=0.0,
        memory_rss_bytes=_BREACH_TREE_RSS_MIB * _BYTES_PER_MIB,
        process_count=_BREACH_TREE_MEMBERS,
    )
    root_only_usage = ProcessTreeUsage(
        cpu_time_seconds=0.0,
        memory_rss_bytes=_BREACH_ROOT_ONLY_RSS_MIB * _BYTES_PER_MIB,
        process_count=1,
    )

    assert process_mod._describe_limit_breach(tree_usage, plan) is not None
    assert process_mod._describe_limit_breach(root_only_usage, plan) is None


@pytest.mark.asyncio
async def test_monitor_kills_process_tree_on_memory_breach() -> None:
    """内存超限时监控必须真的杀掉进程树——B8 的核心兜底。

    上限刻意设在"根进程单独用量"之上：只盯根进程永远不会触发，
    只有统计整棵树才会超限并触发 kill。
    """
    process = await _spawn_ballooning_tree_async()
    root_only_mb = psutil.Process(process.pid).memory_info().rss / _BYTES_PER_MIB
    limit_mb = int(root_only_mb) + _LIMIT_MARGIN_MIB
    assert limit_mb < _BALLOON_MIB, "上限必须落在根进程用量与整棵树用量之间才有区分度"

    executor = process_mod.ProcessExecutor()
    info = process_mod.ProcessInfo(
        process=process,
        run_id="run-memory-breach",
        started_at=datetime.now(),
        exec_plan=_exec_plan(memory_limit_mb=limit_mb, timeout_seconds=_MONITOR_TIMEOUT_SECONDS),
    )

    try:
        await asyncio.wait_for(executor._monitor_resources(info), timeout=_MONITOR_TIMEOUT_SECONDS)
        await asyncio.wait_for(process.wait(), timeout=_MONITOR_TIMEOUT_SECONDS)
    finally:
        _kill_tree(process.pid)
        if process.returncode is None:  # pragma: no cover - 仅断言失败时兜底
            process.kill()
            await process.wait()

    assert process.returncode is not None, "监控必须杀掉超限的进程树"
    assert info.memory_peak_mb > root_only_mb, "峰值内存必须来自整棵进程树，而不是根进程"
    assert info.memory_peak_mb >= limit_mb


# --------------------------------------------------------------------------
# 测试工具
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _CpuTimes:
    user: float
    system: float


@dataclass(frozen=True)
class _MemoryInfo:
    rss: int


def _balloon_tree_code() -> str:
    """根进程只 fork 一个吃内存的子进程，自己几乎不占内存（模拟 bwrap 外壳）。"""
    child = (
        f"blob = bytearray({_BALLOON_MIB} * {_BYTES_PER_MIB});"
        f"blob[::{_TOUCH_STRIDE_BYTES}] = b'x' * len(blob[::{_TOUCH_STRIDE_BYTES}]);"
        "import sys; sys.stdout.write('ready\\n'); sys.stdout.flush();"
        f"import time; time.sleep({_TREE_SLEEP_SECONDS})"
    )
    return (
        "import subprocess, sys, time;"
        f"c = subprocess.Popen([sys.executable, '-c', {child!r}], stdout=subprocess.PIPE);"
        "c.stdout.readline();"
        "sys.stdout.write('ready\\n'); sys.stdout.flush();"
        f"time.sleep({_TREE_SLEEP_SECONDS})"
    )


def _spawn_ballooning_tree() -> subprocess.Popen:
    proc = subprocess.Popen([sys.executable, "-c", _balloon_tree_code()], stdout=subprocess.PIPE, text=True)
    assert proc.stdout is not None
    assert proc.stdout.readline().strip() == "ready", "进程树未就绪"
    return proc


async def _spawn_ballooning_tree_async() -> asyncio.subprocess.Process:
    preexec = process_limits.build_preexec_fn(
        process_limits.SandboxLimits(
            enforce_rlimit=False,
            cpu_seconds=0,
            memory_mb=0,
            max_open_files=0,
            max_processes=0,
            file_size_mb=0,
        )
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        _balloon_tree_code(),
        stdout=asyncio.subprocess.PIPE,
        preexec_fn=preexec,
    )
    assert process.stdout is not None
    line = await asyncio.wait_for(process.stdout.readline(), timeout=_TREE_READY_TIMEOUT_SECONDS)
    assert line.strip() == b"ready", "进程树未就绪"
    return process


def _kill_tree(pid: int) -> None:
    """清理测试拉起的整棵进程树，避免残留进程拖垮后续用例。"""
    with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
        parent = psutil.Process(pid)
        for member in (*parent.children(recursive=True), parent):
            with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                member.kill()
    deadline = time.monotonic() + _KILL_TREE_TIMEOUT_SECONDS
    while time.monotonic() < deadline and psutil.pid_exists(pid):
        time.sleep(_KILL_TREE_POLL_SECONDS)
