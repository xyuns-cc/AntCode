"""B7: ``signal_process_group`` 不得对 Worker 自身的进程组下手。

``os.setsid()`` 一旦失败（或被静默吞掉），子进程会留在 Worker 自己的进程组里，
此时 ``os.killpg`` 会把 Worker 主进程连同所有兄弟任务一起杀掉。这里锁死
"pgid 与自身相同就退回单进程信号" 的兜底，以及正常情况下必须走 killpg。
"""

from __future__ import annotations

import os
import signal
from dataclasses import dataclass, field
from unittest.mock import MagicMock, patch

import pytest
from antcode_worker.executor import process_signals

# 构造一个与 Worker 必然不同的 pgid，避免与真实进程组撞号
_FOREIGN_PGID_OFFSET = 12345
# 一个几乎不可能存在的 pid，仅用于走 getpgid 失败分支
_MISSING_PID = 999999


@dataclass
class _FakeProcess:
    """只暴露 signal_process_group 需要的属性。"""

    pid: int
    returncode: int | None = None
    signalled: list[int] = field(default_factory=list)

    def send_signal(self, sig: int) -> None:
        self.signalled.append(sig)


def test_signal_process_group_refuses_to_kill_own_group(monkeypatch: pytest.MonkeyPatch) -> None:
    """pgid 与 Worker 自身相同时必须拒绝 killpg，否则会自杀并杀光兄弟任务。"""
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(process_signals.os, "killpg", lambda pgid, sig: killed.append((pgid, sig)))
    # 模拟 setsid 失败的后果：子进程 pgid == Worker 自身 pgid
    monkeypatch.setattr(process_signals.os, "getpgid", lambda _pid: os.getpgrp())

    proc = _FakeProcess(pid=os.getpid() + 1)
    process_signals.signal_process_group(proc, signal.SIGKILL)  # type: ignore[arg-type]

    assert killed == [], "绝不能对 Worker 自身的进程组调用 killpg"
    assert proc.signalled == [signal.SIGKILL], "应退回到只向该子进程发信号"


def test_signal_process_group_uses_killpg_for_distinct_group(monkeypatch: pytest.MonkeyPatch) -> None:
    """进程组独立时必须走 killpg，以便连带杀掉孙进程。"""
    killed: list[tuple[int, int]] = []
    foreign_pgid = os.getpgrp() + _FOREIGN_PGID_OFFSET
    monkeypatch.setattr(process_signals.os, "killpg", lambda pgid, sig: killed.append((pgid, sig)))
    monkeypatch.setattr(process_signals.os, "getpgid", lambda _pid: foreign_pgid)

    proc = _FakeProcess(pid=os.getpid() + 1)
    process_signals.signal_process_group(proc, signal.SIGTERM)  # type: ignore[arg-type]

    assert killed == [(foreign_pgid, signal.SIGTERM)]
    assert proc.signalled == [], "killpg 成功后不应再单独给主进程发信号"


def test_signal_process_group_when_returncode_set_is_noop():
    """进程已退出时不再发任何信号，避免误伤复用了 pid 的新进程。"""
    proc = MagicMock()
    proc.returncode = 0

    process_signals.signal_process_group(proc, signal.SIGTERM)

    proc.send_signal.assert_not_called()


def test_signal_process_group_falls_back_to_main_pid_on_oserror():
    """拿不到进程组时退回到只给主进程发信号。"""
    proc = MagicMock()
    proc.returncode = None
    proc.pid = _MISSING_PID

    with patch("antcode_worker.executor.process_signals.os.getpgid", side_effect=OSError):
        process_signals.signal_process_group(proc, signal.SIGTERM)

    proc.send_signal.assert_called_once_with(signal.SIGTERM)
