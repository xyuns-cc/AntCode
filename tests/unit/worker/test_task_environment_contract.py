import os
import sys
from pathlib import Path

from antcode_worker.domain.models import ExecPlan, RuntimeHandle
from antcode_worker.executor.base import ExecutorConfig
from antcode_worker.executor.process import ProcessExecutor
from antcode_worker.executor.sandbox import SandboxConfig, SandboxExecutor


def _runtime(runtimes_root: Path) -> RuntimeHandle:
    runtime_path = runtimes_root / "runtime"
    runtime_path.mkdir(exist_ok=True)
    return RuntimeHandle(
        path=str(runtime_path),
        runtime_hash="hash",
        python_executable=sys.executable,
    )


def _plan() -> ExecPlan:
    return ExecPlan(
        command=sys.executable,
        run_id="test-run",
        plugin_name="code",
        env={
            "BUSINESS_API_KEY": "task-secret",
            "CUSTOM_SETTING": "enabled",
            "LD_PRELOAD": "/tmp/attack.so",
            "PYTHONPATH": "/tmp/attack",
        },
        enforce_rlimit=False,
    )


def test_process_executor_keeps_explicit_task_secrets_only(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("WORKER_API_KEY", "host-secret")

    env = ProcessExecutor()._build_env(_plan(), _runtime(tmp_path))

    assert env["BUSINESS_API_KEY"] == "task-secret"
    assert env["CUSTOM_SETTING"] == "enabled"
    assert env["PYTHONPATH"] == str(tmp_path / "runtime")
    assert "WORKER_API_KEY" not in env
    assert "LD_PRELOAD" not in env


def test_sandbox_executor_keeps_explicit_task_secrets_only(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("WORKER_API_KEY", "host-secret")
    monkeypatch.setenv("PATH", "/usr/bin")
    data_root = tmp_path / "data"
    runtimes_root = tmp_path / "runtimes"
    work_dir = data_root / "runs" / "sources" / "test-run" / "project"
    data_root.mkdir()
    runtimes_root.mkdir()
    work_dir.mkdir(parents=True)
    executor = SandboxExecutor(
        config=ExecutorConfig(),
        sandbox_config=SandboxConfig(
            sandbox_command=["/usr/bin/bwrap"],
            data_dir=str(data_root),
            runtimes_dir=str(runtimes_root),
        ),
    )
    context = {"work_dir": str(work_dir), "plugin_name": "code"}

    runtime = _runtime(runtimes_root)
    plan = executor._create_sandboxed_plan(_plan(), runtime, context)

    assert plan.env["BUSINESS_API_KEY"] == "task-secret"
    assert plan.env["CUSTOM_SETTING"] == "enabled"
    assert plan.env["PYTHONPATH"] == runtime.path
    assert "WORKER_API_KEY" not in plan.env
    assert "LD_PRELOAD" not in plan.env


def test_internal_plugin_receives_worker_source_path(tmp_path: Path):
    plan = _plan()
    plan.plugin_name = "spider"
    data_root = tmp_path / "data"
    runtimes_root = tmp_path / "runtimes"
    work_dir = data_root / "runs" / "sources" / "test-run" / "project"
    data_root.mkdir()
    runtimes_root.mkdir()
    work_dir.mkdir(parents=True)
    executor = SandboxExecutor(
        sandbox_config=SandboxConfig(
            sandbox_command=["/usr/bin/bwrap"],
            data_dir=str(data_root),
            runtimes_dir=str(runtimes_root),
        )
    )

    sandboxed = executor._create_sandboxed_plan(
        plan,
        _runtime(runtimes_root),
        {"work_dir": str(work_dir), "plugin_name": "spider"},
    )

    python_path = sandboxed.env["PYTHONPATH"].split(os.pathsep)
    assert python_path[0] == str(runtimes_root / "runtime")
    assert any(path.endswith("services/worker/src") for path in python_path)
    assert any(path.endswith("packages/antcode_scrapy/src") for path in python_path)
    assert any(path.endswith("packages/antcode_contracts/src") for path in python_path)
