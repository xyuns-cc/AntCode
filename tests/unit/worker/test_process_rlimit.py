"""R3: Worker 进程沙箱 rlimit + 进程组隔离。

直接拉起子进程会很慢且与 CI 平台耦合，这里聚焦验证 preexec 构造逻辑
（应该在期望情况下返回 callable、并且能在缺失某些 RLIMIT 项的平台
上优雅 skip）。进程组信号的健壮性见 ``test_process_group_signals.py``。
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest
from antcode_worker.executor import process_limits

_CPU_SECONDS = 10
_MEMORY_MB = 128
_MAX_OPEN_FILES = 64
_MAX_PROCESSES = 32
# fork 子进程的退出码：2 = preexec 按预期抛错，0 = 静默放行（回归）
_EXIT_PREEXEC_RAISED = 2


def _limits(*, enforce_rlimit: bool, apply_values: bool = True) -> process_limits.SandboxLimits:
    """构造沙箱参数；``apply_values=False`` 时所有上限为 0（即不限制）。"""
    return process_limits.SandboxLimits(
        enforce_rlimit=enforce_rlimit,
        cpu_seconds=_CPU_SECONDS if apply_values else 0,
        memory_mb=_MEMORY_MB if apply_values else 0,
        max_open_files=_MAX_OPEN_FILES if apply_values else 0,
        max_processes=_MAX_PROCESSES if apply_values else 0,
        file_size_mb=0,
    )


def test_preexec_keeps_process_group_when_rlimit_flag_is_false():
    fn = process_limits.build_preexec_fn(_limits(enforce_rlimit=False, apply_values=False))
    assert callable(fn)


def test_preexec_enabled_returns_callable():
    fn = process_limits.build_preexec_fn(_limits(enforce_rlimit=True))
    assert callable(fn)


@pytest.mark.skipif(not process_limits.HAS_RESOURCE, reason="resource module unavailable")
def test_preexec_does_not_apply_rlimits_when_disabled():
    fn = process_limits.build_preexec_fn(_limits(enforce_rlimit=False))
    assert fn is not None

    with (
        patch("antcode_worker.executor.process_limits.os.setsid"),
        patch.object(process_limits.resource, "setrlimit") as setrlimit,
    ):
        fn()

    setrlimit.assert_not_called()


def test_bwrap_skips_host_uid_process_limit():
    # 基线已冻结本用例的魔法数字比较，保持原样以免造成门禁漂移
    assert process_limits.host_max_processes(["/usr/bin/bwrap", "--unshare-user"], 64) == 0
    assert process_limits.host_max_processes(["/usr/bin/python"], 64) == 64


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX only")
def test_preexec_fails_closed_when_rlimit_cannot_be_applied():
    """rlimit 设不上时 preexec 必须抛错，让子进程根本起不来。

    B8：这里原本断言的是「设不上也要优雅放行」（旧名 ..._handles_unavailable_rlimit_gracefully）——
    那正是缺陷本身：CPU/RAM/NOFILE/NPROC/FSIZE 任一项失败，子进程会以**无限制**启动，
    而任务照常上报 SUCCESS。沙箱失效必须是启动失败，不能是静默放行。
    """
    fn = process_limits.build_preexec_fn(_limits(enforce_rlimit=True))
    assert fn is not None

    # preexec 会调 os.setsid()，在 pytest 主进程里直接执行会把测试进程从控制终端摘下来，
    # 因此必须 fork 到子进程里跑。
    with patch("antcode_worker.executor.process_limits.resource.setrlimit", side_effect=OSError("EPERM")):
        pid = os.fork()
        if pid == 0:
            try:
                fn()
            except Exception:
                os._exit(_EXIT_PREEXEC_RAISED)
            os._exit(0)
        _pid, status = os.waitpid(pid, 0)

    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == _EXIT_PREEXEC_RAISED


def test_exec_plan_carries_rlimit_fields():
    from antcode_worker.domain.models import ExecPlan

    plan = ExecPlan(
        command="/usr/bin/true",
        max_open_files=128,
        max_processes=16,
        enforce_rlimit=False,
    )
    assert plan.max_open_files == 128
    assert plan.max_processes == 16
    assert plan.enforce_rlimit is False


def test_executor_config_default_rlimit_on():
    from antcode_worker.executor.base import ExecutorConfig

    cfg = ExecutorConfig()
    assert cfg.enforce_rlimit is True
    assert cfg.default_max_open_files == 2048
    assert cfg.default_max_processes == 64
