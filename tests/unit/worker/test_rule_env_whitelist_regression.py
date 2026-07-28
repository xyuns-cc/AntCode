"""Rule 子进程凭据隔离与可信网络策略回归测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from antcode_worker.domain.enums import TaskType
from antcode_worker.domain.errors import PluginError
from antcode_worker.domain.models import ExecPlan, RunContext, RuntimeHandle, RuntimeSpec, TaskPayload
from antcode_worker.executor.process import ProcessExecutor
from antcode_worker.executor.rule_policy import RULE_PLUGIN_ENV_VARS
from antcode_worker.executor.sandbox import BasicSandbox, SandboxConfig
from antcode_worker.plugins.registry import PluginRegistry
from antcode_worker.plugins.rule.plugin import RulePlugin
from antcode_worker.plugins.spider.plugin import SpiderPlugin


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

    assert (RULE_PLUGIN_ENV_VARS - {"ANTCODE_SPIDER_EGRESS_PROXY"}).issubset(env)
    assert not any("REDIS" in key or "TOKEN" in key or "API_KEY" in key for key in env)
    assert "ANTCODE_WORKER_ID" not in env


@pytest.mark.asyncio
async def test_only_trusted_rule_plan_can_bypass_network_namespace(tmp_path: Path) -> None:
    sandbox = BasicSandbox(
        SandboxConfig(
            network_isolated=True,
            sandbox_command=["/usr/bin/bwrap"],
        )
    )
    rule = ExecPlan(command="python", plugin_name="rule", sandbox_config={"allow_network": True})
    code = ExecPlan(command="python", plugin_name="code", sandbox_config={"allow_network": True})

    rule_context = await sandbox.prepare(rule, str(tmp_path))
    code_context = await sandbox.prepare(code, str(tmp_path))

    assert "--unshare-net" not in sandbox.wrap_command(["python"], rule_context)
    assert "--unshare-net" in sandbox.wrap_command(["python"], code_context)


@pytest.mark.asyncio
async def test_bwrap_recreates_tmp_workdir_before_bind() -> None:
    sandbox = BasicSandbox(SandboxConfig(sandbox_command=["/usr/bin/bwrap"]))
    work_dir = Path("/tmp/antcode-rule/run-1")
    plan = ExecPlan(command="python", plugin_name="rule", sandbox_config={"allow_network": True})

    context = await sandbox.prepare(plan, str(work_dir))
    command = sandbox.wrap_command(["python"], context)

    bind_index = command.index("--bind")
    assert command.index(str(work_dir.parent)) < bind_index
    assert command.index(str(work_dir)) < bind_index


def test_bwrap_applies_process_limit_inside_namespace(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "antcode_worker.executor.sandbox.shutil.which",
        lambda executable: f"/usr/bin/{executable}",
    )
    sandbox = BasicSandbox(SandboxConfig(sandbox_command=["/usr/bin/bwrap"]))
    context = {
        "work_dir": str(tmp_path),
        "allow_network": False,
        "payload_max_processes": 64,
    }

    wrapped = sandbox.wrap_command(["/usr/bin/python", "main.py"], context)
    separator = wrapped.index("--")

    assert wrapped[separator + 1 :] == [
        "/usr/bin/prlimit",
        "--nproc=64:64",
        "--",
        "/usr/bin/python",
        "main.py",
    ]


def test_bwrap_requires_prlimit_for_process_limit(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "antcode_worker.executor.sandbox.shutil.which",
        lambda executable: None,
    )
    sandbox = BasicSandbox(SandboxConfig(sandbox_command=["/usr/bin/bwrap"]))
    context = {
        "work_dir": str(tmp_path),
        "allow_network": False,
        "payload_max_processes": 64,
    }

    with pytest.raises(RuntimeError, match="prlimit"):
        sandbox.wrap_command(["/usr/bin/python", "main.py"], context)
