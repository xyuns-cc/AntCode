"""R3: Worker 进程沙箱 rlimit + 进程组隔离。

直接拉起子进程会很慢且与 CI 平台耦合，这里聚焦验证 preexec 构造逻辑
（应该在期望情况下返回 callable、并且能在缺失某些 RLIMIT 项的平台
上优雅 skip）以及 group-kill helper 的健壮性。
"""

from __future__ import annotations

import os
import signal
import sys
from unittest.mock import MagicMock, patch

import pytest
from antcode_worker.executor import process as process_mod


def test_preexec_keeps_process_group_when_rlimit_flag_is_false():
    fn = process_mod._build_preexec_fn(
        enforce_rlimit=False,
        cpu_seconds=0,
        memory_mb=0,
        max_open_files=0,
        max_processes=0,
    )
    assert callable(fn)


def test_preexec_enabled_returns_callable():
    fn = process_mod._build_preexec_fn(
        enforce_rlimit=True,
        cpu_seconds=10,
        memory_mb=128,
        max_open_files=64,
        max_processes=32,
    )
    assert callable(fn)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX only")
def test_preexec_handles_unavailable_rlimit_gracefully():
    fn = process_mod._build_preexec_fn(
        enforce_rlimit=True,
        cpu_seconds=10,
        memory_mb=128,
        max_open_files=64,
        max_processes=32,
    )
    assert fn is not None

    # 在 macOS 上 RLIMIT_NPROC 设置可能不被支持；用 fork 拉起后立即退出
    # 来验证 preexec 调用本身不会抛异常导致子进程启动失败。
    pid = os.fork()
    if pid == 0:
        try:
            fn()
        except Exception:
            os._exit(2)
        os._exit(0)
    _pid, status = os.waitpid(pid, 0)
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0


def test_signal_process_group_when_returncode_set_is_noop():
    proc = MagicMock()
    proc.returncode = 0
    process_mod._signal_process_group(proc, signal.SIGTERM)
    proc.send_signal.assert_not_called()


def test_signal_process_group_falls_back_to_main_pid_on_oserror():
    proc = MagicMock()
    proc.returncode = None
    proc.pid = 999999

    with patch("antcode_worker.executor.process.os.getpgid", side_effect=OSError):
        process_mod._signal_process_group(proc, signal.SIGTERM)

    proc.send_signal.assert_called_once_with(signal.SIGTERM)


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
