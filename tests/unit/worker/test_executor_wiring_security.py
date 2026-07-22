from types import SimpleNamespace

import pytest
from antcode_worker.app.wiring import _create_executor
from antcode_worker.domain.models import ExecPlan
from antcode_worker.executor import SandboxExecutor


def _config(**overrides):
    values = {
        "max_concurrent_tasks": 1,
        "task_timeout": 60,
        "task_cpu_time_limit_sec": 10,
        "task_memory_limit_mb": 128,
        "sandbox_enforce_rlimit": True,
        "sandbox_max_open_files": 256,
        "sandbox_max_processes": 16,
        "sandbox_mode": "sandbox",
        "sandbox_command": "bwrap",
        "sandbox_network_isolated": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_executor_wiring_requires_real_sandbox_binary(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _binary: None)

    with pytest.raises(RuntimeError, match="沙箱工具不可用"):
        _create_executor(_config())


def test_executor_wiring_rejects_process_mode():
    with pytest.raises(RuntimeError, match="process 已禁用"):
        _create_executor(_config(sandbox_mode="process"))


def test_executor_wiring_builds_network_isolated_sandbox(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _binary: "/usr/bin/bwrap")

    executor = _create_executor(_config())

    assert isinstance(executor, SandboxExecutor)
    assert executor.sandbox_config.network_isolated is True
    # P0-01: wiring 必须把 shutil.which 返回的绝对路径写回 SandboxConfig,
    # 而不是保留 config 里可能为相对名的 "bwrap"。相对路径会被任务 env.PATH 劫持。
    assert executor.sandbox_config.sandbox_command == ["/usr/bin/bwrap"]
    assert executor._payload_process_limit(ExecPlan(command="main.py")) == 16


def test_sandbox_process_limit_can_be_explicitly_disabled(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _binary: "/usr/bin/bwrap")
    executor = _create_executor(_config())

    plan = ExecPlan(command="main.py", enforce_rlimit=False)

    assert executor._payload_process_limit(plan) == 0
