import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from antcode_core.application.services.projects import git_process_limits as limits_module
from antcode_core.application.services.projects import source_bundle_service as source_module
from antcode_core.application.services.projects.git_transfer_quota import GitNetworkLimitExceeded, TransferBudget


def _limits(**overrides):
    values = {
        "timeout_seconds": 1,
        "max_output_bytes": 1024,
        "max_repository_bytes": 1024,
    }
    values.update(overrides)
    return limits_module.GitCommandLimits(**values)


def test_git_command_output_limit_is_enforced_during_process(tmp_path):
    with pytest.raises(ValueError, match="输出超过上限"):
        limits_module.run_bounded_git_command(
            [sys.executable, "-c", "print('x' * 1000)"],
            cwd=tmp_path,
            env={},
            limits=_limits(max_output_bytes=64),
        )


def test_git_command_repository_disk_limit_is_enforced(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    script = "from pathlib import Path; Path('repo/blob').write_bytes(b'x' * 2048)"
    with pytest.raises(ValueError, match="磁盘占用超过上限"):
        limits_module.run_bounded_git_command(
            [sys.executable, "-c", script],
            cwd=tmp_path,
            env={},
            limits=_limits(max_repository_bytes=64),
            quota_path=repo,
        )


def test_external_transfer_failure_terminates_process_immediately(tmp_path):
    started = time.monotonic()
    failure = GitNetworkLimitExceeded(1024)

    with pytest.raises(GitNetworkLimitExceeded, match="1024 字节"):
        limits_module.run_bounded_git_command(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=tmp_path,
            env={},
            limits=_limits(timeout_seconds=30),
            failure_probe=lambda: failure,
        )

    assert time.monotonic() - started < 2


def test_dns_resolver_pool_is_fixed_and_saturates_explicitly():
    def slow_resolver(url):
        time.sleep(0.05)
        return url

    for _ in range(limits_module._DNS_RESOLVER_WORKERS):
        with pytest.raises(TimeoutError):
            limits_module.resolve_with_timeout(slow_resolver, "example.com", 0.001)
    with pytest.raises(RuntimeError, match="已饱和"):
        limits_module.resolve_with_timeout(slow_resolver, "example.com", 0.001)
    assert limits_module._DNS_EXECUTOR._max_workers == limits_module._DNS_RESOLVER_WORKERS
    time.sleep(0.06)


def test_clone_is_shallow_and_checks_out_resolved_revision(monkeypatch, tmp_path):
    commands: list[list[str]] = []
    endpoint = SimpleNamespace(url="https://example.com/repo.git")
    monkeypatch.setattr(source_module, "_resolve_git_endpoint", lambda url: endpoint)
    monkeypatch.setattr(source_module.shutil, "disk_usage", lambda path: SimpleNamespace(free=10**12))
    monkeypatch.setattr(source_module, "validate_repository_metadata", lambda *args, **kwargs: None)

    def run_git(command, **kwargs):
        commands.append(command)
        stdout = revision + "\n" if "rev-parse" in command else ""
        return subprocess.CompletedProcess(command, 0, stdout, "")

    monkeypatch.setattr(source_module, "_run_git", run_git)
    revision = "a" * 40
    source_module._clone_repo(
        tmp_path / "repo",
        {"url": endpoint.url, "ref": "main"},
        revision,
        auth_config=None,
        transfer_budget=TransferBudget(1024),
    )

    clone = commands[0]
    assert "--depth" in clone
    assert "--single-branch" in clone
    assert any(part.startswith("--filter=blob:limit=") for part in clone)
    assert commands[1] == ["git", "checkout", "--detach", revision]
    assert commands[2] == ["git", "rev-parse", "HEAD"]


def test_repository_metadata_object_quota_is_explicit(tmp_path):
    def runner(command, **kwargs):
        del kwargs
        if "count-objects" in command:
            return subprocess.CompletedProcess(command, 0, "count: 3\nin-pack: 8\n", "")
        return subprocess.CompletedProcess(command, 0, "refs/heads/main\n", "")

    with pytest.raises(ValueError, match="对象数超过上限"):
        limits_module.validate_repository_metadata(tmp_path, runner, max_objects=10, max_refs=5)
