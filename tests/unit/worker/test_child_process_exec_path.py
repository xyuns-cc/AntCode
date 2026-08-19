"""子进程真正拿到的 PATH 契约。

与 PYTHONPATH 同源的历史缺陷：``CodePlugin._build_env`` 给 Node 项目拼的
``node_modules/.bin`` 先被 ``sandbox_plan`` 覆盖、再被 ``ProcessExecutor._build_env``
覆盖，Node 项目永远用不上本地 CLI。断言中间值（插件层 / 沙箱层的 PATH）正是那条
假绿的形状，因此本文件的断言一律落在 ``_build_env`` 的返回值与一次真实子进程上。
"""

from __future__ import annotations

import asyncio
import os
import stat
import sys
from pathlib import Path

import pytest
from antcode_worker.domain.models import ExecPlan, RunContext, RuntimeHandle, RuntimeSpec, TaskPayload
from antcode_worker.engine.engine import Engine
from antcode_worker.executor.base import ExecutorConfig
from antcode_worker.executor.process import ProcessExecutor
from antcode_worker.executor.sandbox import SandboxConfig, SandboxExecutor
from antcode_worker.plugins.code.plugin import CodePlugin

RUNTIME_PATH = "/data/worker/runtimes/shared-py312"
PROBE_CLI = "antcode-probe"


def _runtime(path: str = RUNTIME_PATH) -> RuntimeHandle:
    return RuntimeHandle(path=path, runtime_hash="h", python_executable=f"{path}/bin/python")


def _child_path(plan: ExecPlan, runtime: RuntimeHandle | None = None) -> list[str]:
    """ProcessExecutor 交给 create_subprocess_exec 的那份 PATH。"""
    return ProcessExecutor()._build_env(plan, runtime or _runtime())["PATH"].split(os.pathsep)


def _make_node_project(root: Path) -> Path:
    """建出 npm 装完依赖后的样子：``<project_cwd>/node_modules/.bin/<cli>``。"""
    local_bin = root / "node_modules" / ".bin"
    local_bin.mkdir(parents=True)
    probe = local_bin / PROBE_CLI
    probe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    probe.chmod(probe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return local_bin


@pytest.mark.asyncio
async def test_node_local_bin_reaches_the_child_path(tmp_path: Path) -> None:
    """CodePlugin → Engine._stamp_plan_scope → _build_env 全链后本地 CLI 目录必须在。"""
    bundle_root = tmp_path / "extracted"
    project_cwd = bundle_root / "web"
    project_cwd.mkdir(parents=True)
    local_bin = _make_node_project(project_cwd)
    (project_cwd / "app.js").write_text("", encoding="utf-8")

    payload = TaskPayload(
        entry_point="app.js",
        workspace_path=str(bundle_root),
        project_cwd=str(project_cwd),
    )
    context = RunContext(
        run_id="run-1",
        task_id="task-1",
        project_id="proj-1",
        runtime_spec=RuntimeSpec(python_path=f"{RUNTIME_PATH}/bin/python"),
    )
    plan = await CodePlugin().build_plan(context=context, payload=payload)
    Engine._stamp_plan_scope(plan, payload, "run-1")

    assert str(local_bin) in _child_path(plan)


def test_runtime_bin_stays_ahead_of_the_task_controlled_node_bin(tmp_path: Path) -> None:
    """P0-01 不变量：首项恒是 Worker 的 runtime bin，任务目录遮蔽不了它。"""
    project_cwd = tmp_path / "web"
    project_cwd.mkdir()
    local_bin = _make_node_project(project_cwd)
    plan = ExecPlan(command="/bin/sh", plugin_name="code", cwd=str(project_cwd))

    entries = _child_path(plan)

    assert entries[0] == os.path.join(RUNTIME_PATH, "bin")
    assert entries.index(str(local_bin)) == 1
    # 本地 CLI 排在宿主 PATH 之前，才符合 npm "本地依赖优先于全局" 的语义
    assert entries.index(str(local_bin)) < entries.index(os.environ["PATH"].split(os.pathsep)[0])


def test_task_injected_path_never_reaches_the_child(tmp_path: Path) -> None:
    """P0-01 回归：node_modules/.bin 是 Worker 从 cwd 推导的，不是从 env 收下的。

    任务把任意目录写进 ``exec_plan.env['PATH']`` 依旧整条丢弃——放宽的只是
    "Worker 自己算出的项目本地目录"，注入面没有变大。
    """
    project_cwd = tmp_path / "web"
    project_cwd.mkdir()
    local_bin = _make_node_project(project_cwd)
    plan = ExecPlan(
        command="/bin/sh",
        plugin_name="code",
        cwd=str(project_cwd),
        env={"PATH": os.pathsep.join(["/tmp/evil", str(tmp_path / "also-evil")])},
    )

    entries = _child_path(plan)

    assert "/tmp/evil" not in entries
    assert str(tmp_path / "also-evil") not in entries
    assert str(local_bin) in entries


def test_child_path_has_no_node_bin_without_node_modules(tmp_path: Path) -> None:
    """没装 Node 依赖的任务不得凭空多出一段 PATH。"""
    project_cwd = tmp_path / "py"
    project_cwd.mkdir()
    plan = ExecPlan(command="/bin/sh", plugin_name="code", cwd=str(project_cwd))

    assert _child_path(plan) == [os.path.join(RUNTIME_PATH, "bin"), *os.environ["PATH"].split(os.pathsep)]


def test_sandbox_layer_and_child_layer_never_diverge(tmp_path: Path) -> None:
    """根因锁：两层各写一份 PATH，值一旦分叉，长的那份就会被短的静默盖掉。"""
    data_root = tmp_path / "data"
    runtimes_root = tmp_path / "runtimes"
    bundle_root = data_root / "runs" / "sources" / "run-1" / "proj-1" / "deadbeef" / "extracted"
    work_dir = bundle_root / "web"
    work_dir.mkdir(parents=True)
    _make_node_project(work_dir)
    runtime_path = runtimes_root / "runtime"
    runtime_path.mkdir(parents=True)
    runtime = RuntimeHandle(path=str(runtime_path), runtime_hash="h", python_executable=sys.executable)
    plan = ExecPlan(
        command="python",
        run_id="run-1",
        plugin_name="code",
        cwd=str(work_dir),
        workspace_root=str(bundle_root),
        enforce_rlimit=False,
    )
    executor = SandboxExecutor(
        config=ExecutorConfig(),
        sandbox_config=SandboxConfig(
            sandbox_command=["/usr/bin/bwrap"],
            data_dir=str(data_root),
            runtimes_dir=str(runtimes_root),
        ),
    )

    sandboxed = executor._create_sandboxed_plan(plan, runtime, {"work_dir": str(work_dir), "plugin_name": "code"})

    assert sandboxed.env["PATH"] == ProcessExecutor()._build_env(sandboxed, runtime)["PATH"]


@pytest.mark.asyncio
async def test_plugin_never_writes_the_authoritative_env_keys(tmp_path: Path) -> None:
    """锁死死代码不被加回：插件写 PATH/PYTHONPATH 只会被下游原样盖掉。

    bundle 根与项目目录都放上 ``node_modules/.bin``（monorepo 的真实形状），
    这样被删掉的那段插件代码一旦回来就会真的写出 PATH，本例立刻变红。
    """
    bundle_root = tmp_path / "extracted"
    project_cwd = bundle_root / "web"
    project_cwd.mkdir(parents=True)
    _make_node_project(bundle_root)
    _make_node_project(project_cwd)
    (project_cwd / "app.js").write_text("", encoding="utf-8")

    plan = await CodePlugin().build_plan(
        context=RunContext(run_id="r", task_id="t", project_id="p"),
        payload=TaskPayload(
            entry_point="app.js",
            workspace_path=str(bundle_root),
            project_cwd=str(project_cwd),
        ),
    )

    assert "PATH" not in plan.env
    assert "PYTHONPATH" not in plan.env


@pytest.mark.asyncio
async def test_real_child_resolves_the_node_local_cli_by_bare_name(tmp_path: Path) -> None:
    """真起一个子进程：按裸名字 exec 本地 CLI 必须成功且 exit 0。"""
    project_cwd = tmp_path / "web"
    project_cwd.mkdir()
    _make_node_project(project_cwd)

    plan = ExecPlan(
        command="/bin/sh",
        args=["-c", f"command -v {PROBE_CLI} && exec {PROBE_CLI}"],
        run_id="run-real",
        plugin_name="code",
        cwd=str(project_cwd),
        enforce_rlimit=False,
    )
    executor = ProcessExecutor(ExecutorConfig())
    await executor.start()
    try:
        result = await asyncio.wait_for(
            executor.run(plan, RuntimeHandle(path=str(tmp_path), runtime_hash="h", python_executable=sys.executable)),
            timeout=30,
        )
    finally:
        await executor.stop()

    assert result.exit_code == 0
    assert result.error_message is None
