import concurrent.futures
import subprocess
import sys
import threading
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


def test_dns_resolver_pool_is_fixed_and_queues_within_timeout():
    def slow_resolver(url):
        time.sleep(0.05)
        return url

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as callers:
        futures = [
            callers.submit(limits_module.resolve_with_timeout, slow_resolver, f"host-{index}", 1) for index in range(3)
        ]

    assert [future.result() for future in futures] == ["host-0", "host-1", "host-2"]
    assert limits_module._DNS_EXECUTOR._max_workers == limits_module._DNS_RESOLVER_WORKERS


def test_dns_resolver_capacity_wait_is_bounded_by_request_timeout():
    callers_ready = threading.Barrier(limits_module._DNS_RESOLVER_WORKERS + 1)
    release = threading.Event()

    def blocked_resolver(url):
        callers_ready.wait()
        release.wait()
        return url

    with concurrent.futures.ThreadPoolExecutor(max_workers=limits_module._DNS_RESOLVER_WORKERS) as callers:
        active = [
            callers.submit(limits_module.resolve_with_timeout, blocked_resolver, f"host-{index}", 1)
            for index in range(limits_module._DNS_RESOLVER_WORKERS)
        ]
        callers_ready.wait()
        try:
            with pytest.raises(TimeoutError, match="等待执行池"):
                limits_module.resolve_with_timeout(blocked_resolver, "queued-host", 0.01)
        finally:
            release.set()

    assert [future.result() for future in active] == [
        f"host-{index}" for index in range(limits_module._DNS_RESOLVER_WORKERS)
    ]


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


_STDERR_ENV = "FAKE_GIT_STDERR"
# stderr 内容经环境变量喂给子进程，**绝不能**出现在 argv 里：argv 会被
# ``CalledProcessError.__str__`` 原样打进第一行，断言就会在"stderr 根本没被
# 读取"的情况下照样通过——这类用例摘掉修复也不变红，等于没测。
_EMIT_STDERR = [
    sys.executable,
    "-c",
    f"import os, sys; sys.stderr.write(os.environ[{_STDERR_ENV!r}]); raise SystemExit(128)",
]


def test_git_failure_message_carries_git_own_diagnostic(tmp_path):
    """走查实测：远端不可达时用户拿到的是一句纯退出码，和自己填的 URL 毫无关系。

    git 的诊断此前只躺在 ``stderr`` 属性里，``str(exc)`` 从不读它。
    """
    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        limits_module.run_bounded_git_command(
            _EMIT_STDERR,
            cwd=tmp_path,
            env={_STDERR_ENV: "fatal: unable to access: Could not resolve host: nope.invalid\n"},
            limits=_limits(),
        )

    assert "Could not resolve host: nope.invalid" in str(exc_info.value)


def test_git_failure_message_redacts_credentials_from_stderr_and_argv(tmp_path):
    """git 会把带 token 的 remote URL 原样回显，这条消息要进库还要进 HTTP 响应。"""
    secret = "ghp_abcd1234EFGH5678"
    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        limits_module.run_bounded_git_command(
            [*_EMIT_STDERR, f"https://oauth2:{secret}@example.test/r.git"],
            cwd=tmp_path,
            env={_STDERR_ENV: f"fatal: Authentication failed for 'https://oauth2:{secret}@example.test/r.git/'\n"},
            limits=_limits(),
        )

    message = str(exc_info.value)
    # 凭证在 stderr 和 argv 里各出现一次，两处都不许漏出来。
    assert secret not in message
    assert "Authentication failed" in message


def test_git_failure_message_is_bounded_and_keeps_the_tail(tmp_path):
    """stderr 上限是 8MB 的传输闸，不是给人读的长度；结论在末尾，截断保留尾部。"""
    noise = "Receiving objects: 1% \n" * 500
    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        limits_module.run_bounded_git_command(
            _EMIT_STDERR,
            cwd=tmp_path,
            env={_STDERR_ENV: f"{noise}fatal: the real reason\n"},
            limits=_limits(max_output_bytes=65536),
        )

    message = str(exc_info.value)
    assert "fatal: the real reason" in message
    assert len(message) < len(noise)
