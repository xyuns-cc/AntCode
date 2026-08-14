"""Rule 子进程凭据隔离与可信网络策略回归测试。"""

from __future__ import annotations

import os
import socket
import sys
import tempfile
from pathlib import Path

import pytest
from antcode_worker.domain.enums import TaskType
from antcode_worker.domain.errors import PluginError
from antcode_worker.domain.models import ExecPlan, RunContext, RuntimeHandle, RuntimeSpec, TaskPayload
from antcode_worker.executor.process import ProcessExecutor
from antcode_worker.executor.rule_policy import (
    RULE_EGRESS_CONNECTION_LIMIT_CONFIG,
    RULE_EGRESS_DURATION_CONFIG,
    RULE_EGRESS_SOCKET_CONFIG,
    RULE_PLUGIN_ENV_VARS,
    SPIDER_STATS_PATH_ENV,
)
from antcode_worker.executor.sandbox import BasicSandbox, SandboxConfig
from antcode_worker.plugins.registry import PluginRegistry
from antcode_worker.plugins.rule.plugin import RulePlugin
from antcode_worker.plugins.spider.plugin import SpiderPlugin

_MIN_READ_ONLY_BINDS = 2


@pytest.mark.asyncio
async def test_rule_plan_contains_only_local_spool_control_env(tmp_path: Path) -> None:
    context = RunContext(run_id="run-1", task_id="task-1", project_id="project-1")
    payload = TaskPayload(
        task_type=TaskType.RULE,
        project_id="project-1",
        project_cwd=str(tmp_path),
        kwargs={"target_url": "https://example.com", "extraction_rules": [{"field": "title"}]},
        env_vars={
            "WORKER_API_KEY": "api-key",
            "WORKER_GATEWAY_AUTH_TOKEN": "bearer",
            "WORKER_REDIS_URL": "redis://secret",
        },
    )

    plan = await RulePlugin().build_plan(context, payload)

    assert set(plan.env) == RULE_PLUGIN_ENV_VARS - {"ANTCODE_SPIDER_EGRESS_PROXY"}
    assert plan.env["ANTCODE_SPIDER_SINK_MODE"] == "spool"
    assert Path(plan.env["ANTCODE_SPIDER_SPOOL_PATH"]).is_file()
    # P0-03: Rule 默认不放行网络,需 ANTCODE_RULE_ALLOW_NETWORK=1 显式开启。
    assert plan.sandbox_config == {"allow_network": False}


@pytest.mark.asyncio
async def test_spider_plan_and_process_expose_only_local_spool_control_env(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import antcode_worker.plugins.spider.plugin as spider_module

    monkeypatch.setattr(spider_module.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setenv("ANTCODE_SPIDER_REDIS_URL", "redis://host-secret")
    context = RunContext(
        run_id="run-spider",
        task_id="task-1",
        project_id="project-1",
        runtime_spec=RuntimeSpec(python_path="python"),
    )
    payload = TaskPayload(
        task_type=TaskType.SPIDER,
        entry_point="spider.py",
        project_cwd=str(tmp_path),
        project_id="project-1",
    )

    plan = await SpiderPlugin().build_plan(context, payload)
    plan.plugin_name = "spider"
    runtime = RuntimeHandle(path=str(tmp_path), runtime_hash="hash", python_executable="python")
    env = ProcessExecutor()._build_env(plan, runtime)

    assert env["ANTCODE_SPIDER_SINK_MODE"] == "spool"
    assert Path(env["ANTCODE_SPIDER_SPOOL_PATH"]).is_file()
    assert not any("REDIS" in key or "GATEWAY" in key or "REPORTER" in key for key in env)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("unsupported", "message"),
    [
        ({"resume_enabled": True}, "resume_enabled"),
        (
            {
                "proxy_config": {
                    "enabled": True,
                    "proxy_url": "http://proxy.example.com:8080",
                    "username": "worker-user",
                    "password": "worker-secret",
                }
            },
            "proxy_config",
        ),
    ],
)
async def test_unsupported_spool_features_fail_before_rule_file(
    tmp_path: Path,
    unsupported: dict,
    message: str,
) -> None:
    registry = PluginRegistry()
    registry.register(RulePlugin())
    payload = TaskPayload(
        task_type=TaskType.RULE,
        project_id="project-1",
        project_cwd=str(tmp_path),
        kwargs={
            "target_url": "https://example.com",
            "extraction_rules": [{"field": "title"}],
            **unsupported,
        },
    )
    context = RunContext(run_id="run-1", task_id="task-1", project_id="project-1")

    with pytest.raises(PluginError, match=message):
        await registry.build_plan(context, payload)

    assert not (tmp_path / ".antcode-rule").exists()


def test_rule_process_env_drops_host_and_plan_credentials(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ANTCODE_SPIDER_REDIS_URL", "redis://host-secret")
    monkeypatch.setenv("WORKER_REDIS_URL", "redis://worker-secret")
    plan = ExecPlan(
        command="python",
        plugin_name="rule",
        env={
            "ANTCODE_SPIDER_RUN_ID": "run-1",
            "ANTCODE_SPIDER_PROJECT_ID": "project-1",
            "ANTCODE_SPIDER_SINK_MODE": "spool",
            "ANTCODE_SPIDER_SPOOL_PATH": str(tmp_path / "spool.jsonl"),
            "ANTCODE_SPIDER_GATEWAY_AUTH_TOKEN": "bearer",
            "ANTCODE_SPIDER_GATEWAY_API_KEY": "api-key",
            "ANTCODE_WORKER_ID": "worker-1",
        },
    )
    runtime = RuntimeHandle(path=str(tmp_path), runtime_hash="hash", python_executable="python")

    env = ProcessExecutor()._build_env(plan, runtime)

    optional = {"ANTCODE_SPIDER_EGRESS_PROXY", SPIDER_STATS_PATH_ENV}
    assert (RULE_PLUGIN_ENV_VARS - optional).issubset(env)
    assert not any("REDIS" in key or "TOKEN" in key or "API_KEY" in key for key in env)
    assert "ANTCODE_WORKER_ID" not in env


@pytest.mark.asyncio
async def test_rule_cannot_request_shared_network_namespace(tmp_path: Path) -> None:
    sandbox = BasicSandbox(
        SandboxConfig(
            network_isolated=True,
            sandbox_command=["/usr/bin/bwrap"],
        )
    )
    rule = ExecPlan(command="python", plugin_name="rule", sandbox_config={"allow_network": True})

    with pytest.raises(RuntimeError, match="禁止共享"):
        await sandbox.prepare(rule, str(tmp_path))


@pytest.mark.asyncio
async def test_rule_namespace_exposes_only_read_only_unix_bridge(monkeypatch, tmp_path: Path) -> None:
    rule_root = Path(tempfile.gettempdir()) / "antcode-rule"
    rule_root.mkdir(mode=0o700, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="bridge-run-", dir=rule_root) as private_dir:
        work_dir = Path(private_dir)
        run_id = work_dir.name
        work_dir.chmod(0o700)
        bridge_dir = work_dir / ".e-private"
        bridge_dir.mkdir(mode=0o700)
        socket_path = bridge_dir / "p.sock"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(socket_path))
        os.chmod(socket_path, 0o600)
        try:
            # 默认根是 gitignored 的 data/worker/runtimes，冷检出上必被 fail-closed
            runtimes_root = tmp_path / "worker-data" / "runtimes"
            runtimes_root.mkdir(parents=True)
            config = SandboxConfig(
                network_isolated=False,
                sandbox_command=["/usr/bin/bwrap"],
                data_dir=str(runtimes_root.parent),
                runtimes_dir=str(runtimes_root),
            )
            sandbox = BasicSandbox(config)
            plan = ExecPlan(
                command="python",
                run_id=run_id,
                plugin_name="rule",
                sandbox_config={
                    RULE_EGRESS_SOCKET_CONFIG: str(socket_path),
                    RULE_EGRESS_CONNECTION_LIMIT_CONFIG: 4,
                },
            )

            context = await sandbox.prepare(plan, str(work_dir))
            context["payload_max_processes"] = plan.sandbox_config[RULE_EGRESS_CONNECTION_LIMIT_CONFIG]
            context[RULE_EGRESS_DURATION_CONFIG] = 60
            monkeypatch.setattr(
                "antcode_worker.executor.sandbox_provider.shutil.which",
                lambda _name: "/usr/bin/prlimit",
            )
            command = sandbox.wrap_command([sys.executable, "crawl.py"], context)

            assert "--unshare-net" in command
            assert command.count("--ro-bind") >= _MIN_READ_ONLY_BINDS
            work_bind = next(
                index
                for index, value in enumerate(command)
                if value == "--bind" and command[index + 1] == str(work_dir.resolve())
            )
            bridge_bind = next(
                index
                for index, value in enumerate(command)
                if value == "--ro-bind" and command[index + 1] == str(socket_path.parent)
            )
            assert bridge_bind > work_bind
            assert command[bridge_bind + 2] == str(socket_path.parent)
            assert "antcode_worker.executor.rule_network_relay" in command
            assert command.count(str(socket_path)) == 1
            assert "--max-connections" in command
            assert str(plan.sandbox_config[RULE_EGRESS_CONNECTION_LIMIT_CONFIG]) in command
        finally:
            listener.close()


@pytest.mark.asyncio
async def test_bwrap_recreates_tmp_workdir_before_bind(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    work_dir = data_root / "runs" / "sources" / "run-1" / "project"
    work_dir.mkdir(parents=True)
    (data_root / "runtimes").mkdir()
    sandbox = BasicSandbox(SandboxConfig(sandbox_command=["/usr/bin/bwrap"], data_dir=str(data_root)))
    plan = ExecPlan(command=sys.executable, run_id="run-1", plugin_name="code")

    context = await sandbox.prepare(plan, str(work_dir))
    command = sandbox.wrap_command([sys.executable], context)

    bind_index = command.index("--bind")
    assert command.index(str(work_dir.parent)) < bind_index
    assert command[bind_index + 1 : bind_index + 3] == [str(work_dir), str(work_dir)]


def test_bwrap_applies_process_limit_inside_namespace(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "antcode_worker.executor.sandbox_provider.shutil.which",
        lambda executable: f"/usr/bin/{executable}",
    )
    data_root = tmp_path / "data"
    work_dir = data_root / "runs" / "sources" / "run-1" / "project"
    work_dir.mkdir(parents=True)
    (data_root / "runtimes").mkdir()
    sandbox = BasicSandbox(SandboxConfig(sandbox_command=["/usr/bin/bwrap"], data_dir=str(data_root)))
    context = {
        "work_dir": str(work_dir),
        "run_id": "run-1",
        "allow_network": False,
        "payload_max_processes": 64,
    }

    wrapped = sandbox.wrap_command([sys.executable, "main.py"], context)
    separator = wrapped.index("--")

    assert wrapped[separator + 1 :] == [
        "/usr/bin/prlimit",
        "--nproc=64:64",
        "--",
        sys.executable,
        "main.py",
    ]


def test_bwrap_requires_prlimit_for_process_limit(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "antcode_worker.executor.sandbox_provider.shutil.which",
        lambda executable: None,
    )
    data_root = tmp_path / "data"
    work_dir = data_root / "runs" / "sources" / "run-1" / "project"
    work_dir.mkdir(parents=True)
    (data_root / "runtimes").mkdir()
    sandbox = BasicSandbox(SandboxConfig(sandbox_command=["/usr/bin/bwrap"], data_dir=str(data_root)))
    context = {
        "work_dir": str(work_dir),
        "run_id": "run-1",
        "allow_network": False,
        "payload_max_processes": 64,
    }

    with pytest.raises(RuntimeError, match="prlimit"):
        sandbox.wrap_command([sys.executable, "main.py"], context)
