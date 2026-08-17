"""子进程真正拿到的 PYTHONPATH 契约。

历史假绿：插件层与沙箱层各写一份 PYTHONPATH，测试断言的都是这些中间值，而
``ProcessExecutor._build_env`` 才是 ``create_subprocess_exec(env=...)`` 的实参——
它无条件重写 PYTHONPATH，上游补的 bundle 根与受信内建包根从未到达子进程。
本文件的断言一律落在 ``_build_env`` 的返回值（以及一次真实子进程）上。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest
from antcode_worker.domain.models import ExecPlan, RunContext, RuntimeHandle, RuntimeSpec, TaskPayload
from antcode_worker.engine.engine import Engine
from antcode_worker.executor.base import ExecutorConfig
from antcode_worker.executor.process import ProcessExecutor
from antcode_worker.executor.sandbox import SandboxConfig, SandboxExecutor
from antcode_worker.plugins.code.plugin import CodePlugin

BUNDLE_ROOT = "/data/worker/runs/sources/run-1/proj-1/deadbeef/extracted"
PROJECT_CWD = f"{BUNDLE_ROOT}/spiders/news"
RUNTIME_PATH = "/data/worker/runtimes/shared-py312"


def _runtime(path: str = RUNTIME_PATH) -> RuntimeHandle:
    return RuntimeHandle(path=path, runtime_hash="h", python_executable=f"{path}/bin/python")


def _child_env(plan: ExecPlan, runtime: RuntimeHandle | None = None) -> dict[str, str]:
    """返回 ProcessExecutor 交给 create_subprocess_exec 的那份 env。"""
    return ProcessExecutor()._build_env(plan, runtime or _runtime())


@pytest.mark.asyncio
async def test_bundle_root_reaches_the_child_pythonpath() -> None:
    """CodePlugin → Engine._stamp_plan_scope → _build_env 全链后 bundle 根必须在。"""
    payload = TaskPayload(entry_point="main.py", workspace_path=BUNDLE_ROOT, project_cwd=PROJECT_CWD)
    context = RunContext(
        run_id="run-1",
        task_id="task-1",
        project_id="proj-1",
        runtime_spec=RuntimeSpec(python_path=f"{RUNTIME_PATH}/bin/python"),
    )
    plan = await CodePlugin().build_plan(context=context, payload=payload)
    Engine._stamp_plan_scope(plan, payload, "run-1")

    assert _child_env(plan)["PYTHONPATH"].split(os.pathsep) == [RUNTIME_PATH, BUNDLE_ROOT]


def test_internal_plugin_trusted_roots_reach_the_child_pythonpath() -> None:
    """受信内建包源码根排在 bundle 根之前，任务代码无法遮蔽 antcode_*。"""
    plan = ExecPlan(command="python", plugin_name="spider", workspace_root=BUNDLE_ROOT)

    python_path = _child_env(plan)["PYTHONPATH"].split(os.pathsep)

    assert python_path[0] == RUNTIME_PATH
    assert python_path[-1] == BUNDLE_ROOT
    assert any(entry.endswith("services/worker/src") for entry in python_path)
    assert any(entry.endswith("packages/antcode_scrapy/src") for entry in python_path)


def test_sandbox_layer_and_child_layer_never_diverge(tmp_path: Path) -> None:
    """根因锁：两层各写一份 PYTHONPATH，值一旦分叉，长的那份就会被短的静默盖掉。"""
    data_root = tmp_path / "data"
    runtimes_root = tmp_path / "runtimes"
    # ArtifactFetcher 布局：<run_id>/<project_id>/<content_hash>/extracted/<subdir>
    bundle_root = data_root / "runs" / "sources" / "run-1" / "proj-1" / "deadbeef" / "extracted"
    work_dir = bundle_root / "spiders"
    work_dir.mkdir(parents=True)
    runtime_path = runtimes_root / "runtime"
    runtime_path.mkdir(parents=True)
    runtime = RuntimeHandle(path=str(runtime_path), runtime_hash="h", python_executable=sys.executable)
    plan = ExecPlan(
        command="python",
        run_id="run-1",
        plugin_name="spider",
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

    sandboxed = executor._create_sandboxed_plan(plan, runtime, {"work_dir": str(work_dir), "plugin_name": "spider"})

    assert sandboxed.env["PYTHONPATH"] == _child_env(sandboxed, runtime)["PYTHONPATH"]


def test_task_injected_pythonpath_never_reaches_the_child() -> None:
    """P0-01 在真实边界上的回归：任务注入的 PYTHONPATH 一律被权威值盖掉。"""
    plan = ExecPlan(command="python", plugin_name="code", env={"PYTHONPATH": "/tmp/attack"})

    assert _child_env(plan)["PYTHONPATH"] == RUNTIME_PATH


def test_child_env_carries_no_bundle_root_without_a_source_bundle() -> None:
    """没有 source bundle 的任务不得凭空多出一段 PYTHONPATH。"""
    plan = ExecPlan(command="python", plugin_name="code")

    assert _child_env(plan)["PYTHONPATH"] == RUNTIME_PATH


@pytest.mark.asyncio
async def test_real_child_imports_shared_package_from_the_bundle_root(tmp_path: Path) -> None:
    """真起一个子进程：``import libs.common.helper`` 必须成功且 exit 0。"""
    bundle_root = tmp_path / "extracted"
    project_cwd = bundle_root / "spiders" / "news"
    project_cwd.mkdir(parents=True)
    for package in (bundle_root / "libs", bundle_root / "libs" / "common"):
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
    (bundle_root / "libs" / "common" / "helper.py").write_text("MARK = 'shared-ok'\n", encoding="utf-8")
    entry = project_cwd / "main.py"
    entry.write_text("import libs.common.helper as h\nprint(h.MARK)\n", encoding="utf-8")

    plan = ExecPlan(
        command=str(entry),
        run_id="run-real",
        plugin_name="code",
        cwd=str(project_cwd),
        workspace_root=str(bundle_root),
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
