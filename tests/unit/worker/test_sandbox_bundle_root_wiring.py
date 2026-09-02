"""source bundle 根目录必须从 fetcher 一路传到 bwrap 挂载参数。

只修 `sandbox_mounts` 是不够的：`ExecPlan.workspace_root` 任一环断链，
include_paths 共享目录就又会在沙箱里消失，而且不报错——这里逐环钉死。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_worker.domain.models import ExecPlan, SourceBundle
from antcode_worker.engine.engine import Engine
from antcode_worker.executor.sandbox_config import SandboxConfig
from antcode_worker.executor.sandbox_plan import SandboxPlanRequest, create_sandboxed_plan
from antcode_worker.executor.sandbox_provider import BasicSandbox
from antcode_worker.projects.fetcher import FetchedWorkspace

BUNDLE_ROOT = "/data/worker/runs/sources/run-1/proj-1/deadbeef/extracted"
PROJECT_CWD = f"{BUNDLE_ROOT}/src"


def test_basic_sandbox_prepare_carries_workspace_root_into_context() -> None:
    sandbox = BasicSandbox(SandboxConfig(enabled=True, sandbox_command=["/usr/bin/bwrap"]))
    plan = ExecPlan(command="python", cwd=PROJECT_CWD, workspace_root=BUNDLE_ROOT, run_id="run-1")

    context = _run(sandbox.prepare(plan, PROJECT_CWD))

    assert context["workspace_root"] == BUNDLE_ROOT


def test_sandbox_filesystem_request_receives_the_bundle_root(monkeypatch: pytest.MonkeyPatch) -> None:
    sandbox = BasicSandbox(SandboxConfig(enabled=True, sandbox_command=["/usr/bin/bwrap"], data_dir="/data/worker"))
    captured: dict[str, object] = {}

    def _capture(request):
        captured["bundle_root"] = request.bundle_root
        captured["work_dir"] = request.work_dir
        return ()

    monkeypatch.setattr("antcode_worker.executor.sandbox_provider.sandbox_filesystem_args", _capture)
    monkeypatch.setattr(BasicSandbox, "_resolve_work_dir", staticmethod(lambda context: Path(PROJECT_CWD)))
    monkeypatch.setattr(BasicSandbox, "_require_bwrap", lambda self: None)

    sandbox.wrap_command(
        ["python", "main.py"],
        {"work_dir": PROJECT_CWD, "workspace_root": BUNDLE_ROOT, "tmpfs_size_mb": 512},
    )

    assert captured == {"bundle_root": Path(BUNDLE_ROOT), "work_dir": Path(PROJECT_CWD)}


def test_sandboxed_plan_copy_keeps_the_bundle_root() -> None:
    plan = ExecPlan(command="python", cwd=PROJECT_CWD, workspace_root=BUNDLE_ROOT, run_id="run-1")
    sandbox = MagicMock()
    sandbox.wrap_command.return_value = ["/usr/bin/bwrap", "--", "python"]
    sandbox.filter_env.return_value = {}
    runtime = MagicMock(path="/data/worker/runtimes/h", python_executable="/usr/bin/python3", runtime_hash="h")

    copied = create_sandboxed_plan(
        sandbox,
        MagicMock(default_max_processes=0),
        SandboxPlanRequest(exec_plan=plan, runtime_handle=runtime, context={"work_dir": PROJECT_CWD}),
    )

    assert copied.workspace_root == BUNDLE_ROOT


@pytest.mark.asyncio
async def test_engine_stamps_the_fetched_bundle_root_on_the_exec_plan() -> None:
    plans: list[ExecPlan] = []
    engine = _engine_with_source_bundle(plans)
    context = MagicMock(
        run_id="run-1",
        project_id="proj-1",
        task_id="task-1",
        labels={},
        runtime_spec=None,
        timeout_seconds=60,
        memory_limit_mb=0,
        cpu_limit_seconds=0,
        receipt=None,
    )
    await engine.state_manager.add_if_new("run-1", "task-1")

    await engine._execute_task(context, _source_bundle_message())

    assert [plan.workspace_root for plan in plans] == [BUNDLE_ROOT]


def _engine_with_source_bundle(plans: list[ExecPlan]) -> Engine:
    transport = MagicMock()
    transport.report_result = AsyncMock(return_value=True)
    transport.ack_task = AsyncMock(return_value=True)
    transport.authoritative_now_ms = AsyncMock(return_value=1_000)
    transport._lease_id = "lease-test"
    transport._worker_id = "worker-test"
    executor = MagicMock()
    executor.run = AsyncMock(side_effect=RuntimeError("停在执行前即可，只验证 plan 的字段"))
    fetcher = MagicMock()
    fetcher.fetch = AsyncMock(return_value=FetchedWorkspace(bundle_root=BUNDLE_ROOT, project_cwd=PROJECT_CWD))
    fetcher.cleanup = AsyncMock()
    registry = MagicMock()

    async def _build_plan(context, payload):
        del context, payload
        plan = ExecPlan(command="python", cwd=PROJECT_CWD)
        plans.append(plan)
        return plan

    registry.build_plan = _build_plan
    return Engine(
        transport=transport,
        executor=executor,
        runtime_manager=MagicMock(release=AsyncMock()),
        plugin_registry=registry,
        project_fetcher=fetcher,
    )


def _source_bundle_message() -> MagicMock:
    task_msg = MagicMock()
    task_msg.task_id = "task-1"
    task_msg.project_id = "proj-1"
    task_msg.project_type = "code"
    task_msg.params = {}
    task_msg.environment = {}
    task_msg.timeout = 60
    task_msg.priority = 0
    task_msg.entry_point = "main.py"
    task_msg.traceparent = ""
    task_msg.source_subdir = "src"
    task_msg.source_bundle = SourceBundle(uri="pgartifact://" + "a" * 64, sha256="a" * 64, size=10)
    return task_msg


def _run(coroutine):
    import asyncio

    return asyncio.run(coroutine)
