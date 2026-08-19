from pathlib import Path

import pytest
from antcode_worker.domain.enums import TaskType
from antcode_worker.domain.models import RunContext, TaskPayload
from antcode_worker.executor.sandbox import BasicSandbox, SandboxConfig
from antcode_worker.plugins.code.plugin import CodePlugin


@pytest.mark.asyncio
async def test_go_external_dependencies_require_vendor(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.test/app\n\ngo 1.22\n", encoding="utf-8")
    (tmp_path / "main.go").write_text("package main\nfunc main() {}\n", encoding="utf-8")
    (tmp_path / "go.mod").write_text(
        "module example.test/app\n\ngo 1.22\n\nrequire (\n  example.test/lib v1.0.0\n)\n",
        encoding="utf-8",
    )
    payload = TaskPayload(
        task_type=TaskType.CODE,
        entry_point="main.go",
        project_cwd=str(tmp_path),
        workspace_path=str(tmp_path),
        env_vars={"GOMODCACHE": "/shared/host-cache"},
    )

    with pytest.raises(RuntimeError, match="必须提交 vendor"):
        await CodePlugin().build_plan(RunContext("run-1", "task-1", "project-1"), payload)


@pytest.mark.asyncio
async def test_go_vendor_project_runs_offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 计划构建要求 go 真的在 PATH 上（缺运行时是 fail-closed 的，不再退回裸命令名），
    # 而开发机/CI 未必装 Go；这里放一个占位可执行文件，只为让路径解析有解。
    stub_bin = tmp_path / "stub-bin"
    stub_bin.mkdir()
    go_stub = stub_bin / "go"
    go_stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    go_stub.chmod(0o755)
    monkeypatch.setenv("PATH", str(stub_bin))
    (tmp_path / "go.mod").write_text(
        "module example.test/app\n\ngo 1.22\n\nrequire example.test/lib v1.0.0\n",
        encoding="utf-8",
    )
    (tmp_path / "main.go").write_text("package main\nfunc main() {}\n", encoding="utf-8")
    (tmp_path / "vendor").mkdir()
    payload = TaskPayload(
        task_type=TaskType.CODE,
        entry_point="main.go",
        project_cwd=str(tmp_path),
        workspace_path=str(tmp_path),
        env_vars={},
    )
    plan = await CodePlugin().build_plan(RunContext("run-1", "task-1", "project-1"), payload)

    assert plan.command == str(go_stub)
    assert plan.args[:2] == ["run", "main.go"]
    assert plan.env["GOMODCACHE"].startswith(str(tmp_path))
    assert plan.env["GOCACHE"].startswith(str(tmp_path))
    assert plan.env["GOENV"] == "off"
    assert plan.env["GOWORK"] == "off"
    assert plan.env["GOTOOLCHAIN"] == "local"
    assert plan.env["GOPROXY"] == "off"
    assert plan.env["GOFLAGS"] == "-mod=vendor"


@pytest.mark.asyncio
async def test_go_work_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.test/app\n\ngo 1.22\n", encoding="utf-8")
    (tmp_path / "go.work").write_text("go 1.22\nuse ../outside\n", encoding="utf-8")
    (tmp_path / "main.go").write_text("package main\nfunc main() {}\n", encoding="utf-8")
    payload = TaskPayload(
        task_type=TaskType.CODE,
        entry_point="main.go",
        project_cwd=str(tmp_path),
        workspace_path=str(tmp_path),
    )

    with pytest.raises(RuntimeError, match="不支持 go.work"):
        await CodePlugin().build_plan(RunContext("run-1", "task-1", "project-1"), payload)


def test_sandbox_allows_only_explicit_go_execution_state() -> None:
    sandbox = BasicSandbox(SandboxConfig(sandbox_command=["bwrap"]))
    env = {
        "GOCACHE": "/workspace/cache/build",
        "GOMODCACHE": "/workspace/cache/modules",
        "GOENV": "off",
        "GOWORK": "off",
        "GOTOOLCHAIN": "local",
        "GOPROXY": "off",
        "GOFLAGS": "-mod=mod",
        "DATABASE_URL": "postgresql://secret",
    }

    filtered = sandbox.filter_env(env, {"plugin_name": "code"})

    assert set(filtered) == {"GOCACHE", "GOMODCACHE", "GOENV", "GOWORK", "GOTOOLCHAIN", "GOPROXY", "GOFLAGS"}
