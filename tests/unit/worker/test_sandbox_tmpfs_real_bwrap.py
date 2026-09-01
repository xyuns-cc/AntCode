"""真机臂：``--size`` 真的把 ``/tmp`` 限住了吗。

与 ``test_sandbox_tmpfs_budget`` 的分工：那边全是参数构造与纯函数，只能证明"命令行
怎么拼"；这边证明内核照做了——``--size`` 真的把这个挂载点限住，且 ``write()`` 写下去
的字节**不进任何进程的 RSS**（这正是原注释声称"由进程树 RSS 监控兜底"错在哪里）。

范围（必须知情）：**本文件只写 ``/tmp``**，结论也只对 ``/tmp`` 成立，不要推广到"沙箱
这条路径"。沙箱一共有四个 tmpfs，另外两个（newroot 自身与 ``--dev`` 建的 ``/dev``）
拿不到 ``--size``，走的是另一套边界，见 ``test_sandbox_unsized_tmpfs_seal``——只测被
修好的那个挂载点、再把结论推广出去，正是它们当初漏掉的原因。

非 Linux / 没有 bwrap 的开发机上整份跳过。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time

import psutil
import pytest
from antcode_worker.executor.resource_sampler import ProcessTreeUsage, sample_process_tree
from antcode_worker.executor.sandbox_config import SandboxConfig
from antcode_worker.executor.sandbox_provider import BasicSandbox

# 小到跑得快，又要显著高于 sh/dd 自身几 MB 的 RSS 才有区分度
_REAL_CAP_MB = 64
_REAL_WRITE_MB = 32
_ALIVE_SECONDS = 10
_SAMPLE_POLL_SECONDS = 0.1


def _run_sandboxed_writer(tmp_path, *, cap_mb: int, write_mb: int) -> tuple[ProcessTreeUsage, int]:
    """在真 bwrap 沙箱里往 /tmp 写 ``write_mb``，写完保持存活让采样器读到用量。"""
    sandbox = BasicSandbox(
        SandboxConfig(
            sandbox_command=[shutil.which("bwrap") or ""],
            network_isolated=True,
            data_dir=str(tmp_path),
            runtimes_dir=str(tmp_path / "runtimes"),
        )
    )
    work_dir = tmp_path / "runs" / "sources" / "run-1" / "project"
    work_dir.mkdir(parents=True)
    (tmp_path / "runtimes").mkdir()
    script = (
        f"dd if=/dev/zero of=/tmp/blk bs=1M count={write_mb} 2>/dev/null; echo $? > /tmp/rc; sleep {_ALIVE_SECONDS}"
    )
    command = sandbox.wrap_command(
        ["/bin/sh", "-c", script],
        {"work_dir": str(work_dir), "plugin_name": "code", "run_id": "run-1", "tmpfs_size_mb": cap_mb},
    )
    process = subprocess.Popen(command, env={"PATH": "/usr/bin:/bin"})  # noqa: S603
    try:
        deadline = time.monotonic() + _ALIVE_SECONDS
        usage = None
        while time.monotonic() < deadline:
            usage = sample_process_tree(psutil.Process(process.pid))
            if usage is not None and usage.scratch_used_bytes > 0:
                break
            time.sleep(_SAMPLE_POLL_SECONDS)
        assert usage is not None, "采样器必须至少采到一次"
        return usage, process.pid
    finally:
        process.kill()
        process.wait(timeout=_ALIVE_SECONDS)


@pytest.mark.skipif(sys.platform != "linux" or shutil.which("bwrap") is None, reason="需要 Linux bubblewrap")
def test_real_sandbox_scratch_bytes_are_visible_only_to_the_scratch_sampler(tmp_path) -> None:
    """真机臂：``write()`` 写进沙箱 tmpfs 的页**不进任何进程的 RSS**。

    这是全文件唯一能证明"容器里真发生了什么"的一组用例。一正一反同时断言：
    内存盘采样看得见这些字节（正），而 RSS 求和看不见（反）——后者正是本次修复
    的起因，注释里原本声称"由进程树 RSS 监控兜底"就是错在这里。
    """
    usage, _pid = _run_sandboxed_writer(tmp_path, cap_mb=_REAL_CAP_MB, write_mb=_REAL_WRITE_MB)

    assert usage.scratch_used_mb >= _REAL_WRITE_MB, "内存盘采样必须看见写下去的字节"
    assert usage.memory_rss_mb < _REAL_WRITE_MB, "这些字节绝不该出现在进程树 RSS 里"
    assert not usage.scratch_exhausted, "没写满就不能报成写满"


@pytest.mark.skipif(sys.platform != "linux" or shutil.which("bwrap") is None, reason="需要 Linux bubblewrap")
def test_real_sandbox_scratch_is_capped_at_the_configured_size(tmp_path) -> None:
    """真机臂：--size 真的把内存盘限住，且写满能被观测到。

    没有它，上面那些"参数拼对了"的断言并不能说明内核照做了。
    """
    usage, _pid = _run_sandboxed_writer(tmp_path, cap_mb=_REAL_CAP_MB, write_mb=_REAL_CAP_MB * 2)

    assert usage.scratch_used_mb <= _REAL_CAP_MB, f"内存盘用量不得超过上限 {_REAL_CAP_MB}MB"
    assert usage.scratch_exhausted, "写满必须被观测到，否则 ENOSPC 无法归因"
