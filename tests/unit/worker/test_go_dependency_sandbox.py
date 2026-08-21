from pathlib import Path

import pytest
from antcode_worker.domain.enums import TaskType
from antcode_worker.domain.models import RunContext, TaskPayload
from antcode_worker.executor.sandbox import BasicSandbox, SandboxConfig
from antcode_worker.plugins.code.plugin import CodePlugin
from antcode_worker.runtime import go_execution_policy
from antcode_worker.runtime.dependency_process import DependencyCommandResult

_BINARY_RELPATH = Path(".antcode-go-cache") / "bin" / "task-binary"
# memory_limit_mb 用真机 worker-03 的生效限额（3GiB×70%÷4）：CodePlugin 要靠它把
# 预算注进沙箱内读不到 cgroup 的 Go 工具链。
_RUN_CONTEXT = RunContext("run-1", "task-1", "project-1", memory_limit_mb=537)


def _install_go_stub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把 PATH 指到一个占位 go 上。

    计划构建要求 go 真的在 PATH 上（缺运行时是 fail-closed 的，不再退回裸命令名），
    而开发机/CI 未必装 Go。这里换 PATH 而不是 patch ``shutil.which`` 本身，免得把
    被测的解析逻辑一起短路掉。占位文件只能证明"argv 怎么拼"，证明不了真实 Go
    工具链的行为——退出码保真必须在装了 Go 的 Worker 上实跑才算数。
    """
    stub_bin = tmp_path / "stub-bin"
    stub_bin.mkdir()
    go_stub = stub_bin / "go"
    go_stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    go_stub.chmod(0o755)
    monkeypatch.setenv("PATH", str(stub_bin))
    return go_stub


def _record_build_commands(
    monkeypatch: pytest.MonkeyPatch,
    *,
    exit_code: int = 0,
    stderr: str = "",
) -> list[tuple[list[str], dict[str, str]]]:
    """拦下装配期子进程，记录 (command, env)。真沙箱要 bwrap，单测跑不了。"""
    calls: list[tuple[list[str], dict[str, str]]] = []

    async def fake_run(command, *, cwd, env, limits):
        del cwd, limits
        calls.append((list(command), dict(env)))
        return DependencyCommandResult(exit_code=exit_code, stdout="", stderr=stderr)

    monkeypatch.setattr(go_execution_policy, "run_dependency_command", fake_run)
    return calls


def _go_payload(tmp_path: Path, **overrides) -> TaskPayload:
    return TaskPayload(
        task_type=TaskType.CODE,
        entry_point="main.go",
        project_cwd=str(tmp_path),
        workspace_path=str(tmp_path),
        **overrides,
    )


def _write_vendored_module(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text(
        "module example.test/app\n\ngo 1.22\n\nrequire example.test/lib v1.0.0\n",
        encoding="utf-8",
    )
    (tmp_path / "main.go").write_text("package main\nfunc main() {}\n", encoding="utf-8")
    (tmp_path / "vendor").mkdir()


@pytest.mark.asyncio
async def test_go_external_dependencies_require_vendor(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.test/app\n\ngo 1.22\n", encoding="utf-8")
    (tmp_path / "main.go").write_text("package main\nfunc main() {}\n", encoding="utf-8")
    (tmp_path / "go.mod").write_text(
        "module example.test/app\n\ngo 1.22\n\nrequire (\n  example.test/lib v1.0.0\n)\n",
        encoding="utf-8",
    )
    payload = _go_payload(tmp_path, env_vars={"GOMODCACHE": "/shared/host-cache"})

    with pytest.raises(RuntimeError, match="必须提交 vendor"):
        await CodePlugin().build_plan(_RUN_CONTEXT, payload)


@pytest.mark.asyncio
async def test_go_vendor_project_runs_offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_go_stub(tmp_path, monkeypatch)
    _record_build_commands(monkeypatch)
    _write_vendored_module(tmp_path)
    payload = _go_payload(tmp_path, env_vars={})

    plan = await CodePlugin().build_plan(_RUN_CONTEXT, payload)

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
    payload = _go_payload(tmp_path)

    with pytest.raises(RuntimeError, match="不支持 go.work"):
        await CodePlugin().build_plan(_RUN_CONTEXT, payload)


@pytest.mark.asyncio
async def test_go_plan_execs_built_binary_instead_of_go_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``go run`` 把子程序退出码折成 1，argv 必须落在编译产物上。

    终验的控制组实证：程序 ``os.Exit(9)`` 时 run 记录里 ``exit_code=1``，
    真值只在 stderr 的 ``exit status 9``。只要 argv[0] 还是 go，这条就修不掉。
    """
    go_stub = _install_go_stub(tmp_path, monkeypatch)
    _record_build_commands(monkeypatch)
    _write_vendored_module(tmp_path)
    payload = _go_payload(tmp_path, args=["--flag", "value"])

    plan = await CodePlugin().build_plan(_RUN_CONTEXT, payload)

    assert plan.command == str(tmp_path / _BINARY_RELPATH)
    assert plan.command != str(go_stub)
    # 程序参数直达产物；"run" / entry_point 都不该再出现在任务 argv 里
    assert plan.args == ["--flag", "value"]


@pytest.mark.asyncio
async def test_go_build_runs_in_offline_sandbox_without_task_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """编译走装配期子进程：命令、产物路径、离线状态、env 隔离逐项钉死。"""
    go_stub = _install_go_stub(tmp_path, monkeypatch)
    calls = _record_build_commands(monkeypatch)
    _write_vendored_module(tmp_path)
    payload = _go_payload(tmp_path, env_vars={"DATABASE_PASSWORD": "s3cret"})

    await CodePlugin().build_plan(_RUN_CONTEXT, payload)

    assert len(calls) == 1
    command, env = calls[0]
    assert command == [str(go_stub), "build", "-o", str(tmp_path / _BINARY_RELPATH), "main.go"]
    assert (tmp_path / _BINARY_RELPATH).parent.is_dir()
    assert env["GOPROXY"] == "off"
    assert env["GOFLAGS"] == "-mod=vendor"
    assert env["GOCACHE"].startswith(str(tmp_path))
    assert env["HOME"] == str(tmp_path)
    assert "DATABASE_PASSWORD" not in env


@pytest.mark.asyncio
async def test_go_build_failure_surfaces_compiler_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """编译失败必须显式抛错，不能带着不存在的产物往下走。"""
    _install_go_stub(tmp_path, monkeypatch)
    _record_build_commands(monkeypatch, exit_code=2, stderr="./main.go:2:15: undefined: nope")
    _write_vendored_module(tmp_path)
    payload = _go_payload(tmp_path)

    with pytest.raises(RuntimeError, match="Go 编译失败.*undefined: nope"):
        await CodePlugin().build_plan(_RUN_CONTEXT, payload)


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
