import json
import os
import sys

import pytest
from antcode_worker.runtime import node_dependency_errors as errors
from antcode_worker.runtime import node_dependency_policy as policy
from antcode_worker.runtime import node_lockfile_policy as lockfile_policy
from antcode_worker.runtime.command_logging import format_command_for_log
from antcode_worker.runtime.dependency_process import DependencyCommandResult, DependencyLimits
from antcode_worker.runtime.uv_manager import run_command


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


def test_node_policy_accepts_real_lockfile_metadata_urls(tmp_path):
    """`npm i -D tsx` 生成的原始 lockfile 必须能过。

    ``funding.url``（赞助页）与 ``deprecated``（维护者留言）都是元数据，不是包的
    下载来源；旧实现对"任何含 :// 的标量"一律套 registry 白名单，于是真实世界的
    lockfile 基本用不了——JS/TS 唯一受支持的离线依赖路径实质不可用。
    """
    _package_json(tmp_path)
    data = {
        "name": "sample",
        "lockfileVersion": 3,
        "packages": {
            "": {"dependencies": {"left-pad": "1.3.0"}},
            "node_modules/left-pad": {
                "version": "1.3.0",
                "resolved": "https://registry.npmjs.org/left-pad/-/left-pad-1.3.0.tgz",
                "funding": {"type": "opencollective", "url": "https://opencollective.com/eslint"},
                "deprecated": "request has been deprecated, see https://github.com/request/request/issues/3142",
            },
            "node_modules/second": {
                "version": "2.0.0",
                "resolved": "https://registry.npmjs.org/second/-/second-2.0.0.tgz",
                "funding": ["https://github.com/sponsors/a", {"url": "https://opencollective.com/b"}],
            },
        },
    }
    (tmp_path / "package-lock.json").write_text(json.dumps(data), encoding="utf-8")

    assert policy.validate_node_dependency_metadata(str(tmp_path)) == "https://registry.npmjs.org"


def test_node_policy_still_checks_resolved_of_package_named_funding(tmp_path):
    """元数据豁免只作用于叶子标量，不剪整棵子树。

    lockfile v1 的 ``dependencies`` 是 name→entry，npm 上真存在名为 funding /
    deprecated 的包；按 key 剪子树会让这些包自己的 ``resolved`` 逃掉检查。
    """
    _package_json(tmp_path)
    data = {
        "name": "sample",
        "lockfileVersion": 1,
        "dependencies": {
            "funding": {"version": "1.0.0", "resolved": "https://attacker.invalid/funding.tgz"},
        },
    }
    (tmp_path / "package-lock.json").write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(errors.NodeDependencyRejected) as excinfo:
        policy.validate_node_dependency_metadata(str(tmp_path))

    assert excinfo.value.error_code == errors.NODE_DEP_REGISTRY_REJECTED


def test_node_policy_rejects_pnpm_lockfile_at_validation_time(tmp_path):
    """pnpm 此前是"校验层宣称支持、镜像里根本没装"，只能在执行期炸一句无关的话。"""
    _package_json(tmp_path)
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")

    with pytest.raises(errors.NodeDependencyRejected) as excinfo:
        policy.validate_node_dependency_metadata(str(tmp_path))

    assert excinfo.value.error_code == errors.NODE_DEP_PACKAGE_MANAGER_UNSUPPORTED
    assert "pnpm-lock.yaml" in excinfo.value.detail


@pytest.mark.asyncio
async def test_node_install_never_shells_out_to_pnpm(tmp_path):
    """红线：不许静默降级到 npm，也不许把不存在的 pnpm 交给沙箱去 exec。"""
    _package_json(tmp_path)
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")
    (tmp_path / ".antcode-deps" / "npm-cache").mkdir(parents=True)
    limits = DependencyLimits(timeout_seconds=60, cpu_seconds=30, memory_mb=256)

    with pytest.raises(errors.NodeDependencyRejected) as excinfo:
        await policy.install_node_dependencies(str(tmp_path), limits=limits)

    assert excinfo.value.error_code == errors.NODE_DEP_PACKAGE_MANAGER_UNSUPPORTED


def test_node_policy_yarn_message_does_not_advertise_pnpm(tmp_path):
    _package_json(tmp_path)
    (tmp_path / "yarn.lock").write_text("# yarn lockfile v1\n", encoding="utf-8")

    with pytest.raises(errors.NodeDependencyRejected) as excinfo:
        policy.validate_node_dependency_metadata(str(tmp_path))

    assert excinfo.value.error_code == errors.NODE_DEP_PACKAGE_MANAGER_UNSUPPORTED
    assert "package-lock.json" in excinfo.value.detail


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
    monkeypatch.setattr(lockfile_policy.settings, "NODE_PACKAGE_REGISTRY_ALLOWLIST", registry)
    with pytest.raises(RuntimeError, match="registry 配置不安全"):
        lockfile_policy.allowed_registries()


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
    (tmp_path / ".antcode-deps" / "npm-cache").mkdir(parents=True)
    captured = {}

    async def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return DependencyCommandResult(0, "", "")

    monkeypatch.setenv("DATABASE_URL", "postgresql://secret")
    monkeypatch.setattr(policy, "run_dependency_command", fake_run)
    limits = DependencyLimits(timeout_seconds=60, cpu_seconds=30, memory_mb=256)
    await policy.install_node_dependencies(str(tmp_path), limits=limits)

    assert "--ignore-scripts" in captured["command"]
    assert "--offline" in captured["command"]
    assert "--registry" in captured["command"]
    assert captured["limits"] == limits
    assert "DATABASE_URL" not in captured["env"]
    assert captured["env"]["npm_config_userconfig"] == os.devnull


@pytest.mark.asyncio
async def test_node_install_requires_workspace_offline_cache(tmp_path):
    _package_json(tmp_path)
    _package_lock(tmp_path)
    limits = DependencyLimits(timeout_seconds=60, cpu_seconds=30, memory_mb=256)

    with pytest.raises(RuntimeError, match="缺少离线缓存"):
        await policy.install_node_dependencies(str(tmp_path), limits=limits)


@pytest.mark.asyncio
async def test_node_install_requires_lockfile_for_declared_dependencies(tmp_path):
    _package_json(tmp_path)
    (tmp_path / ".antcode-deps" / "npm-cache").mkdir(parents=True)
    limits = DependencyLimits(timeout_seconds=60, cpu_seconds=30, memory_mb=256)

    with pytest.raises(RuntimeError, match="必须提供 lockfile"):
        await policy.install_node_dependencies(str(tmp_path), limits=limits)


@pytest.mark.asyncio
async def test_run_command_stops_process_when_output_exceeds_limit():
    result = await run_command(
        [sys.executable, "-c", "print('x' * 1000)"],
        timeout=2,
        max_output_bytes=64,
    )
    assert result.exit_code == 125
    assert "输出超过上限" in result.stderr
