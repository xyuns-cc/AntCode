import json
import os
import sys

import pytest
from antcode_worker.runtime import node_dependency_policy as policy
from antcode_worker.runtime.command_logging import format_command_for_log
from antcode_worker.runtime.uv_manager import CommandResult, run_command


def _package_json(root, dependencies=None):
    (root / "package.json").write_text(
        json.dumps({"name": "sample", "dependencies": dependencies or {"left-pad": "1.3.0"}}),
        encoding="utf-8",
    )


def _package_lock(root, resolved="https://registry.npmjs.org/left-pad/-/left-pad-1.3.0.tgz"):
    data = {
        "name": "sample",
        "lockfileVersion": 3,
        "packages": {
            "": {"dependencies": {"left-pad": "1.3.0"}},
            "node_modules/left-pad": {"version": "1.3.0", "resolved": resolved},
        },
    }
    (root / "package-lock.json").write_text(json.dumps(data), encoding="utf-8")


def test_node_policy_rejects_lockfile_url_outside_registry(tmp_path):
    _package_json(tmp_path)
    _package_lock(tmp_path, "https://attacker.invalid/left-pad.tgz")
    with pytest.raises(ValueError, match="不在允许 registry"):
        policy.validate_node_dependency_metadata(str(tmp_path))


def test_node_policy_rejects_project_package_manager_config(tmp_path):
    _package_json(tmp_path)
    (tmp_path / ".npmrc").write_text("registry=https://attacker.invalid\n", encoding="utf-8")
    with pytest.raises(ValueError, match="不受信任"):
        policy.validate_node_dependency_metadata(str(tmp_path))


def test_node_policy_rejects_direct_git_dependency(tmp_path):
    _package_json(tmp_path, {"owned": "git+https://attacker.invalid/owned.git"})
    with pytest.raises(ValueError, match="registry semver"):
        policy.validate_node_dependency_metadata(str(tmp_path))


@pytest.mark.parametrize(
    "registry",
    [
        "https://user:password@registry.example/npm",
        "https://registry.example/npm?token=secret",
        "https://registry.example/npm#token=secret",
    ],
)
def test_node_policy_rejects_registry_credentials(monkeypatch, registry):
    monkeypatch.setattr(policy.settings, "NODE_PACKAGE_REGISTRY_ALLOWLIST", registry)
    with pytest.raises(RuntimeError, match="registry 配置不安全"):
        policy._allowed_registries()


def test_command_log_redacts_registry_and_index_values():
    rendered = format_command_for_log(
        [
            "npm",
            "ci",
            "--registry",
            "https://registry.example/private/token-value",
            "--index-url=https://user:password@example.invalid/simple",
        ]
    )

    assert "token-value" not in rendered
    assert "password" not in rendered
    assert rendered.count("<redacted>") == 2


@pytest.mark.asyncio
async def test_node_install_disables_scripts_and_drops_worker_environment(monkeypatch, tmp_path):
    _package_json(tmp_path)
    _package_lock(tmp_path)
    captured = {}

    async def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return CommandResult(0, "", "")

    monkeypatch.setenv("DATABASE_URL", "postgresql://secret")
    monkeypatch.setattr(policy, "run_command", fake_run)
    await policy.install_node_dependencies(str(tmp_path))

    assert "--ignore-scripts" in captured["command"]
    assert "--registry" in captured["command"]
    assert captured["inherit_env"] is False
    assert "DATABASE_URL" not in captured["env"]
    assert captured["env"]["npm_config_userconfig"] == os.devnull


@pytest.mark.asyncio
async def test_run_command_stops_process_when_output_exceeds_limit():
    result = await run_command(
        [sys.executable, "-c", "print('x' * 1000)"],
        timeout=2,
        max_output_bytes=64,
    )
    assert result.exit_code == 125
    assert "输出超过上限" in result.stderr
