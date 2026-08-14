"""B7/B8: 子进程沙箱构造（setsid + rlimit）的 fail-closed 行为。

B7 —— ``os.setsid()`` 失败被静默吞掉后，子进程仍留在 Worker 自己的进程组里，
``signal_process_group`` 拿到的是 Worker 自身的 pgid，``os.killpg`` 会把 Worker
主进程和所有兄弟任务一起杀掉。

B8 —— ``setrlimit`` 失败被静默吞掉后子进程以无限制运行，而调用方与 master 毫无
感知，任务照常上报 SUCCESS。

这里验证的是真实行为：真 fork、真 rlimit，不 mock 被测函数本身。
注意 ``preexec_fn`` 里的 ``os.setsid()`` 绝不能在 pytest 主进程里直接调用
（会把测试进程从控制终端上摘下来），所有需要真实执行它的用例一律走 fork。
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Callable

import pytest
from antcode_worker.domain.models import ExecPlan
from antcode_worker.executor import process as process_mod
from antcode_worker.executor import process_limits

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX only")

_CPU_SECONDS = 10
_MEMORY_MB = 128
_MAX_OPEN_FILES = 64
_MAX_PROCESSES = 32
_TIMEOUT_SECONDS = 60
_PROCESS_WAIT_TIMEOUT_SECONDS = 30

# fork 子进程用来区分失败原因的退出码
_EXIT_EXPECTED_ERROR = 7
_EXIT_UNEXPECTED = 8
_EXIT_NO_ERROR = 9
# 子进程回传 pgid 的管道读取上限（十进制 pid 远小于此）
_PGID_PIPE_BYTES = 32


def _sandbox_limits(*, memory_mb: int = 0, enforce_rlimit: bool = True) -> process_limits.SandboxLimits:
    """构造沙箱参数；默认不含 RLIMIT_AS（macOS 上恒不可设）。"""
    return process_limits.SandboxLimits(
        enforce_rlimit=enforce_rlimit,
        cpu_seconds=_CPU_SECONDS,
        memory_mb=memory_mb,
        max_open_files=_MAX_OPEN_FILES,
        max_processes=_MAX_PROCESSES,
        file_size_mb=0,
    )


def _build_preexec(*, memory_mb: int = 0) -> Callable[[], None]:
    """构造真实的 preexec_fn。"""
    return process_limits.build_preexec_fn(_sandbox_limits(memory_mb=memory_mb))


def _launch_spec() -> process_mod._LaunchSpec:
    return process_mod._LaunchSpec(
        cmd=[sys.executable, "-c", "pass"],
        env={"PATH": os.environ.get("PATH", "")},
        cwd=os.getcwd(),
        exec_plan=ExecPlan(command=sys.executable, run_id="run-1", timeout_seconds=_TIMEOUT_SECONDS),
    )


def _run_preexec_in_fork(preexec: Callable[[], None], expected: type[BaseException]) -> int:
    """在 fork 出的子进程里真实执行 preexec，返回退出码。"""
    pid = os.fork()
    if pid == 0:
        try:
            preexec()
        except expected:
            os._exit(_EXIT_EXPECTED_ERROR)
        except BaseException:
            os._exit(_EXIT_UNEXPECTED)
        os._exit(_EXIT_NO_ERROR)
    _pid, status = os.waitpid(pid, 0)
    assert os.WIFEXITED(status), "子进程应正常退出而不是被信号打死"
    return os.WEXITSTATUS(status)


# --------------------------------------------------------------------------
# B7: setsid 失败必须拒绝启动子进程
# --------------------------------------------------------------------------


def test_preexec_propagates_setsid_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """setsid 失败必须抛出，不能静默让子进程留在 Worker 的进程组里。"""

    def _boom() -> int:
        raise OSError(1, "setsid denied")

    monkeypatch.setattr(process_limits.os, "setsid", _boom)
    preexec = _build_preexec()

    with pytest.raises(OSError, match="setsid denied"):
        preexec()


@pytest.mark.asyncio
async def test_spawn_refuses_process_when_setsid_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """setsid 失败时必须拒绝启动子进程，而不是带着错误的进程组继续跑。"""

    def _boom() -> int:
        raise OSError(1, "setsid denied")

    monkeypatch.setattr(process_limits.os, "setsid", _boom)
    executor = process_mod.ProcessExecutor()

    with pytest.raises(RuntimeError, match="子进程沙箱初始化失败"):
        await executor._spawn_process(_launch_spec())


def test_preexec_creates_new_session_in_real_child() -> None:
    """真实 fork 出的子进程必须落在新的进程组里，killpg 才不会误伤 Worker。"""
    preexec = _build_preexec()
    worker_pgid = os.getpgrp()

    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(read_fd)
        try:
            preexec()
            os.write(write_fd, str(os.getpgrp()).encode())
            os._exit(0)
        except BaseException:
            os._exit(_EXIT_UNEXPECTED)
    os.close(write_fd)
    raw = os.read(read_fd, _PGID_PIPE_BYTES)
    os.close(read_fd)
    _pid, status = os.waitpid(pid, 0)

    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
    child_pgid = int(raw.decode())
    assert child_pgid == pid, "子进程必须成为新进程组的组长"
    assert child_pgid != worker_pgid, "子进程绝不能留在 Worker 自己的进程组里"


# --------------------------------------------------------------------------
# B8: rlimit 设置失败必须拒绝启动子进程
# --------------------------------------------------------------------------


def test_preexec_propagates_setrlimit_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """setrlimit 失败必须抛出，不能让子进程以无限制状态运行。"""

    def _boom(_kind: int, _limits: tuple[int, int]) -> None:
        raise ValueError("current limit exceeds maximum limit")

    monkeypatch.setattr(process_limits.resource, "setrlimit", _boom)

    assert _run_preexec_in_fork(_build_preexec(), ValueError) == _EXIT_EXPECTED_ERROR


def test_preexec_propagates_setrlimit_oserror(monkeypatch: pytest.MonkeyPatch) -> None:
    """内核拒绝（EINVAL/EPERM）同样必须让子进程起不来。"""

    def _boom(_kind: int, _limits: tuple[int, int]) -> None:
        raise OSError(22, "Invalid argument")

    monkeypatch.setattr(process_limits.resource, "setrlimit", _boom)

    assert _run_preexec_in_fork(_build_preexec(), OSError) == _EXIT_EXPECTED_ERROR


@pytest.mark.asyncio
async def test_spawn_refuses_process_when_setrlimit_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """rlimit 未生效时必须拒绝启动，并在错误里带上请求的限制值。"""

    def _boom(_kind: int, _limits: tuple[int, int]) -> None:
        raise OSError(22, "Invalid argument")

    monkeypatch.setattr(process_limits.resource, "setrlimit", _boom)
    executor = process_mod.ProcessExecutor()

    with pytest.raises(RuntimeError) as excinfo:
        await executor._spawn_process(_launch_spec())

    message = str(excinfo.value)
    assert "拒绝在无沙箱状态下执行" in message
    assert "nofile=" in message, "错误信息必须带上请求的限制，否则无法定位是哪一项没生效"


@pytest.mark.asyncio
async def test_spawn_succeeds_when_limits_apply() -> None:
    """反向覆盖：限制真的能设上时，子进程必须正常启动（不是一律拒绝）。"""
    executor = process_mod.ProcessExecutor()

    process = await executor._spawn_process(_launch_spec())
    try:
        assert await asyncio.wait_for(process.wait(), timeout=_PROCESS_WAIT_TIMEOUT_SECONDS) == 0
    finally:
        if process.returncode is None:  # pragma: no cover - 兜底
            process.kill()
            await process.wait()


def test_preflight_rejects_unsupported_rlimit_before_fork() -> None:
    """本平台缺少某个 RLIMIT 常量时，必须在 fork 之前就明确拒绝并点名该项。"""
    requested = (process_limits.RlimitRequest("RLIMIT_DOES_NOT_EXIST", 1),)

    with pytest.raises(RuntimeError, match="不支持资源限制项 RLIMIT_DOES_NOT_EXIST"):
        process_limits.preflight_rlimit_support(requested)


def test_disabled_rlimit_still_isolates_process_group() -> None:
    """enforce_rlimit=False 也必须保留 setsid——进程组隔离是安全终止的前提。"""
    preexec = process_limits.build_preexec_fn(_sandbox_limits(memory_mb=_MEMORY_MB, enforce_rlimit=False))

    pid = os.fork()
    if pid == 0:
        try:
            preexec()
            os._exit(0 if os.getpgrp() == os.getpid() else _EXIT_UNEXPECTED)
        except BaseException:
            os._exit(_EXIT_UNEXPECTED)
    _pid, status = os.waitpid(pid, 0)

    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
