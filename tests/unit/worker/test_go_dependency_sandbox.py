from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_worker.domain.enums import TaskType
from antcode_worker.domain.models import RunContext, TaskPayload
from antcode_worker.executor.sandbox import BasicSandbox, SandboxConfig
from antcode_worker.plugins.code.plugin import CodePlugin
from antcode_worker.runtime import go_execution_policy


@pytest.mark.asyncio
async def test_go_dependencies_prefetch_in_prep_then_sandbox_runs_offline(monkeypatch, tmp_path: Path) -> None:
    """P2 §4.4: 生产沙箱默认 --unshare-net —— Go 模块必须在 prep 阶段
    （有网络）经 `go mod download` 预热到工作区缓存，沙箱内 `go run`
    以 GOPROXY=off 离线使用缓存。"""
    (tmp_path / "go.mod").write_text("module example.test/app\n\ngo 1.22\n", encoding="utf-8")
    (tmp_path / "main.go").write_text("package main\nfunc main() {}\n", encoding="utf-8")
    run_command = AsyncMock(return_value=SimpleNamespace(exit_code=0, stdout="", stderr=""))
    monkeypatch.setattr(go_execution_policy, "run_command", run_command)
    monkeypatch.setattr(go_execution_policy.shutil, "which", lambda _name: "/usr/local/bin/go")
    payload = TaskPayload(
        task_type=TaskType.CODE,
        entry_point="main.go",
        project_cwd=str(tmp_path),
        workspace_path=str(tmp_path),
        env_vars={"GOMODCACHE": "/shared/host-cache"},
    )

    plan = await CodePlugin().build_plan(RunContext("run-1", "task-1", "project-1"), payload)

    # prep 阶段真实预取：go mod download，缓存落在工作区内
    run_command.assert_awaited_once()
    argv = run_command.await_args.args[0]
    assert argv[1:] == ["mod", "download"]
    prep_env = run_command.await_args.kwargs["env"]
    assert prep_env["GOMODCACHE"].startswith(str(tmp_path))
    assert run_command.await_args.kwargs["inherit_env"] is False

    # 沙箱执行计划：离线（GOPROXY=off）、缓存指向工作区
    assert plan.args[:2] == ["run", "main.go"]
    assert plan.env["GOMODCACHE"].startswith(str(tmp_path))
    assert plan.env["GOCACHE"].startswith(str(tmp_path))
    assert plan.env["GOENV"] == "off"
    assert plan.env["GOTOOLCHAIN"] == "local"
    assert plan.env["GOPROXY"] == "off"


@pytest.mark.asyncio
async def test_go_vendor_project_skips_prefetch(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.test/app\n\ngo 1.22\n", encoding="utf-8")
    (tmp_path / "main.go").write_text("package main\nfunc main() {}\n", encoding="utf-8")
    (tmp_path / "vendor").mkdir()
    run_command = AsyncMock()
    monkeypatch.setattr(go_execution_policy, "run_command", run_command)

    payload = TaskPayload(
        task_type=TaskType.CODE,
        entry_point="main.go",
        project_cwd=str(tmp_path),
        workspace_path=str(tmp_path),
        env_vars={},
    )
    await CodePlugin().build_plan(RunContext("run-1", "task-1", "project-1"), payload)

    run_command.assert_not_awaited()


def test_sandbox_allows_only_explicit_go_execution_state() -> None:
    sandbox = BasicSandbox(SandboxConfig(sandbox_command=["bwrap"]))
    env = {
        "GOCACHE": "/workspace/cache/build",
        "GOMODCACHE": "/workspace/cache/modules",
        "GOENV": "off",
        "GOTOOLCHAIN": "local",
        "GOPROXY": "off",
        "GOFLAGS": "-mod=mod",
        "DATABASE_URL": "postgresql://secret",
    }

    filtered = sandbox.filter_env(env, {"plugin_name": "code"})

    assert set(filtered) == {"GOCACHE", "GOMODCACHE", "GOENV", "GOTOOLCHAIN", "GOPROXY", "GOFLAGS"}
