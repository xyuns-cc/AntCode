"""P0-01 回归：Worker 沙箱启动命令必须使用绝对路径 + 任务 env 不得污染 PATH。

审查文档 docs/code-review-2026-07-22-round3-review.md 的 P0-01:
wiring 用 shutil.which 校验 bwrap 但存储相对命令名,sandbox.wrap_command
和 process.py 都会让 asyncio.create_subprocess_exec 用 exec_plan.env 里的
PATH 解析可执行文件,workspace 里的伪造 bwrap 会在真隔离建立前以 Worker
容器用户身份被执行,--unshare-* 等硬化全部无效。

本测试锁死三条不变量:
1. BasicSandbox.wrap_command 拒绝非绝对路径的 sandbox_command[0]
2. exec_plan.env 里的 PATH 不会污染沙箱化 ExecPlan 的最终 env(强制走 os.environ)
3. exec_plan.env 里的 LD_PRELOAD / LD_LIBRARY_PATH / BASH_ENV 一律剥离
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest
from antcode_worker.domain.models import ExecPlan, RuntimeHandle
from antcode_worker.executor.base import ExecutorConfig
from antcode_worker.executor.sandbox import (
    BasicSandbox,
    SandboxConfig,
    SandboxExecutor,
)


def _make_runtime_handle(tmp_path):
    return RuntimeHandle(
        path=str(tmp_path / "runtime"),
        runtime_hash="test-hash-p0-01",
        python_executable="/usr/bin/python3",
    )


def test_wrap_command_rejects_relative_sandbox_command():
    """P0-01 前门:相对 sandbox_command[0] 直接被 wrap_command 拒绝。"""
    sandbox = BasicSandbox(SandboxConfig(sandbox_command=["bwrap"]))

    with pytest.raises(RuntimeError, match="沙箱启动命令必须是绝对路径"):
        sandbox.wrap_command(["/bin/true"], {"work_dir": "/tmp/work"})


def test_wrap_command_rejects_relative_non_bwrap_sandbox_command():
    """P0-01:非 bwrap 的其他外部沙箱(firejail 等)同样必须绝对路径。"""
    sandbox = BasicSandbox(SandboxConfig(sandbox_command=["firejail", "--noprofile"]))

    with pytest.raises(RuntimeError, match="沙箱启动命令必须是绝对路径"):
        sandbox.wrap_command(["/bin/true"], {"work_dir": "/tmp/work"})


def test_wrap_command_accepts_absolute_sandbox_command():
    """P0-01:绝对路径正常放行。"""
    sandbox = BasicSandbox(SandboxConfig(sandbox_command=["/usr/bin/bwrap"], fs_isolated=True))

    wrapped = sandbox.wrap_command(["/bin/true"], {"work_dir": "/tmp/work", "plugin_name": "rule"})

    assert wrapped[0] == "/usr/bin/bwrap"
    assert "/bin/true" in wrapped


def test_task_env_path_does_not_pollute_final_env(tmp_path, monkeypatch):
    """P0-01 关键不变量:exec_plan.env 里 PATH=/tmp/evil 不会污染最终子进程 env。

    如果这条测试失败,意味着攻击者可通过任务环境把 PATH 指向自己 workspace,
    让 asyncio.create_subprocess_exec 解析相对命令时命中恶意程序。修复后
    _create_sandboxed_plan 会用 os.environ.PATH 强制覆盖 filtered_env["PATH"]。
    """
    # 模拟 Worker 启动时的真实 PATH(不包含 /tmp/evil)
    worker_path = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    monkeypatch.setenv("PATH", worker_path)

    executor = SandboxExecutor(
        config=ExecutorConfig(),
        sandbox_config=SandboxConfig(sandbox_command=["/usr/bin/bwrap"], fs_isolated=True),
    )

    exec_plan = ExecPlan(
        command="/bin/true",
        args=[],
        env={
            "PATH": "/tmp/evil:/usr/bin",  # 攻击注入
            "LD_PRELOAD": "/tmp/evil/hijack.so",  # 进程劫持
            "LD_LIBRARY_PATH": "/tmp/evil/libs",
            "BASH_ENV": "/tmp/evil/rc",
            "PYTHONPATH": "/tmp/evil/py",  # 覆盖 runtime
        },
        run_id="test-run-p0-01",
        plugin_name="rule",
        enforce_rlimit=False,  # 避免 macOS 上 prlimit 不可用
    )
    runtime_handle = _make_runtime_handle(tmp_path)
    context = {"work_dir": str(tmp_path / "work"), "plugin_name": "rule"}
    os.makedirs(context["work_dir"], exist_ok=True)

    sandboxed_plan = executor._create_sandboxed_plan(exec_plan, runtime_handle, context)

    # PATH 必须是 Worker 权威值,不能是任务注入的 /tmp/evil
    assert sandboxed_plan.env.get("PATH") == worker_path
    assert "/tmp/evil" not in sandboxed_plan.env.get("PATH", "")

    # LD_* / BASH_ENV / ENV 全部剥离
    assert "LD_PRELOAD" not in sandboxed_plan.env
    assert "LD_LIBRARY_PATH" not in sandboxed_plan.env
    assert "LD_AUDIT" not in sandboxed_plan.env
    assert "BASH_ENV" not in sandboxed_plan.env
    assert "ENV" not in sandboxed_plan.env

    # PYTHONPATH 被强制为 runtime.path,不是任务的 /tmp/evil/py
    assert sandboxed_plan.env.get("PYTHONPATH") == runtime_handle.path
    assert sandboxed_plan.env.get("VIRTUAL_ENV") == runtime_handle.path

    # sandbox_command[0] 是绝对路径,任务 PATH 无法改变解析结果
    assert os.path.isabs(sandboxed_plan.command)
    assert sandboxed_plan.command == "/usr/bin/bwrap"


def test_filter_env_still_allows_benign_task_env(tmp_path, monkeypatch):
    """P0-01 反面:剥离攻击向量,但白名单内的其他任务 env(如 LANG)不受影响。"""
    monkeypatch.setenv("PATH", "/usr/bin")

    executor = SandboxExecutor(
        config=ExecutorConfig(),
        sandbox_config=SandboxConfig(sandbox_command=["/usr/bin/bwrap"], fs_isolated=True),
    )

    exec_plan = ExecPlan(
        command="/bin/true",
        args=[],
        env={"LANG": "en_US.UTF-8", "GOFLAGS": "-mod=mod"},
        run_id="test-run-benign",
        plugin_name="code",
        enforce_rlimit=False,
    )
    runtime_handle = _make_runtime_handle(tmp_path)
    context = {"work_dir": str(tmp_path / "work"), "plugin_name": "code"}
    os.makedirs(context["work_dir"], exist_ok=True)

    sandboxed_plan = executor._create_sandboxed_plan(exec_plan, runtime_handle, context)

    # 白名单内变量应保留
    assert sandboxed_plan.env.get("LANG") == "en_US.UTF-8"
    assert sandboxed_plan.env.get("GOFLAGS") == "-mod=mod"
