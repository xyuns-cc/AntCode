import os
import shlex
import socket
import socketserver
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest
from antcode_core.application.services.projects.git_process_limits import (
    GitCommandLimits,
    run_bounded_git_command,
)
from antcode_core.application.services.projects.git_transfer_quota import (
    GitNetworkLimitExceeded,
    TransferBudget,
    pinned_tcp_relay,
)
from antcode_core.application.services.projects.git_transport import (
    GitTransportSession,
    build_git_env,
    git_transport,
)
from antcode_core.application.services.projects.git_url_security import ResolvedURL
from antcode_core.common.config import Settings
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[3]
MIN_TRANSFER_BYTES = 1_048_576
MAX_TRANSFER_BYTES = 8_589_934_592


class _EchoHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        while data := self.request.recv(4096):
            self.request.sendall(data)


@contextmanager
def _echo_server():
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _EchoHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield int(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _open_relay(endpoint):
    client = socket.create_connection((endpoint.host, endpoint.port), timeout=5)
    client.sendall(f"{endpoint.token}\n".encode("ascii"))
    return client


def _settings(**values):
    return Settings(
        DATABASE_URL="postgresql://antcode:secret@127.0.0.1:5432/antcode",
        REDIS_URL="redis://127.0.0.1:6379/0",
        **values,
    )


def _git_env_entries(env: dict[str, str]) -> dict[str, str]:
    return {
        env[f"GIT_CONFIG_KEY_{index}"]: env[f"GIT_CONFIG_VALUE_{index}"]
        for index in range(int(env["GIT_CONFIG_COUNT"]))
    }


def test_pinned_tcp_relay_allows_normal_bidirectional_traffic() -> None:
    budget = TransferBudget(64)
    with _echo_server() as origin_port:
        with pinned_tcp_relay("127.0.0.1", origin_port, budget=budget) as endpoint:
            with _open_relay(endpoint) as client:
                client.sendall(b"binary\x00payload")
                assert client.recv(64) == b"binary\x00payload"
    assert budget.used_bytes == 28


def test_pinned_tcp_relay_disconnects_on_shared_limit() -> None:
    budget = TransferBudget(15)
    with pytest.raises(GitNetworkLimitExceeded, match="15 字节"):
        with _echo_server() as origin_port:
            with pinned_tcp_relay("127.0.0.1", origin_port, budget=budget) as endpoint:
                with _open_relay(endpoint) as first:
                    first.sendall(b"1234")
                    assert first.recv(4) == b"1234"
                with _open_relay(endpoint) as second:
                    second.sendall(b"5678")
                    assert second.recv(4) == b""
    assert budget.used_bytes == 12


def test_transfer_budget_remains_shared_across_relay_instances() -> None:
    budget = TransferBudget(15)
    with _echo_server() as origin_port:
        with pinned_tcp_relay("127.0.0.1", origin_port, budget=budget) as first_endpoint:
            with _open_relay(first_endpoint) as first:
                first.sendall(b"1234")
                assert first.recv(4) == b"1234"
        with pytest.raises(GitNetworkLimitExceeded, match="15 字节"):
            with pinned_tcp_relay("127.0.0.1", origin_port, budget=budget) as second_endpoint:
                with _open_relay(second_endpoint) as second:
                    second.sendall(b"5678")
                    assert second.recv(4) == b""


def test_proxy_command_helper_forwards_binary_stdio() -> None:
    payload = bytes(range(64))
    budget = TransferBudget(len(payload) * 2)
    with _echo_server() as origin_port:
        with pinned_tcp_relay("127.0.0.1", origin_port, budget=budget) as endpoint:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "antcode_core.application.services.projects.git_ssh_proxy",
                    str(endpoint.port),
                    endpoint.token,
                ],
                input=payload,
                capture_output=True,
                timeout=5,
                check=True,
            )
    assert result.stdout == payload


def test_ssh_git_env_uses_pinned_relay_and_host_key_alias() -> None:
    endpoint = ResolvedURL(
        url="ssh://git@example.com/repo.git",
        scheme="ssh",
        host="example.com",
        port=22,
        addresses=("93.184.216.34",),
    )
    with git_transport(endpoint, TransferBudget(1024)) as transport:
        command = build_git_env(None, endpoint, transport)["GIT_SSH_COMMAND"]

    assert "Hostname=93.184.216.34" in command
    assert "HostKeyAlias=example.com" in command
    assert "ProxyCommand=none" not in command
    assert "git_ssh_proxy" in command
    assert command.index("ProxyCommand=") < command.index("ProxyJump=none")
    assert f"-F {os.devnull}" in command
    args = shlex.split(command)
    rendered = subprocess.run(
        [args[0], "-G", *args[1:], "git@example.com"],
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    ).stdout.lower()
    assert "hostname 93.184.216.34" in rendered
    assert "hostkeyalias example.com" in rendered
    assert "proxycommand " in rendered


def test_git_env_rejects_inherited_config_and_external_network_paths(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "protocol.ext.allow")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "always")
    monkeypatch.setenv("GIT_SSH_COMMAND", "attacker-ssh")
    monkeypatch.setenv("HTTP_PROXY", "http://attacker-proxy")
    transport = GitTransportSession(safe_config_dir=str(tmp_path))

    env = build_git_env(None, None, transport)
    entries = _git_env_entries(env)

    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_CONFIG_GLOBAL"] == os.devnull
    assert env["GIT_LFS_SKIP_SMUDGE"] == "1"
    assert "GIT_SSH_COMMAND" not in env
    assert "HTTP_PROXY" not in env
    assert entries["protocol.file.allow"] == "never"
    assert entries["protocol.ext.allow"] == "never"
    assert entries["submodule.recurse"] == "false"
    assert entries["fetch.recurseSubmodules"] == "false"
    assert entries["core.hooksPath"] == str(tmp_path)


def test_malicious_global_filter_hook_and_submodule_config_are_ignored(monkeypatch, tmp_path) -> None:
    repo = _create_filter_repository(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    marker = tmp_path / "executed"
    filter_script = _write_filter_script(tmp_path, marker)
    global_config = home / ".gitconfig"
    _write_malicious_git_config(global_config, filter_script, tmp_path / "hooks", marker=marker)
    monkeypatch.setenv("HOME", str(home))
    budget = TransferBudget(MIN_TRANSFER_BYTES)

    with git_transport(None, budget) as transport:
        env = build_git_env(None, None, transport)
        _run_local_git(["git", "checkout", "--", "payload.txt"], repo, env)
        recurse = _run_local_git(["git", "config", "--bool", "--get", "submodule.recurse"], repo, env)

    assert not marker.exists()
    assert (repo / "payload.txt").read_text(encoding="utf-8") == "raw-content\n"
    assert recurse.stdout.strip() == "false"


def _create_filter_repository(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / ".gitattributes").write_text("*.txt filter=evil\n", encoding="utf-8")
    (repo / "payload.txt").write_text("raw-content\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repo, check=True)
    (repo / "payload.txt").unlink()
    return repo


def _write_filter_script(tmp_path: Path, marker: Path) -> Path:
    script = tmp_path / "evil-filter.sh"
    script.write_text(f"#!/bin/sh\ntouch {marker}\ncat\n", encoding="utf-8")
    script.chmod(0o700)
    return script


def _write_malicious_git_config(path: Path, script: Path, hooks: Path, *, marker: Path) -> None:
    hooks.mkdir()
    hook = hooks / "post-checkout"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    hook.chmod(0o700)
    path.write_text(
        f'[filter "evil"]\n\tsmudge = {script}\n'
        f"[core]\n\thooksPath = {hooks}\n"
        "[submodule]\n\trecurse = true\n"
        "[fetch]\n\trecurseSubmodules = true\n"
        '[protocol "ext"]\n\tallow = always\n',
        encoding="utf-8",
    )


def _run_local_git(command: list[str], cwd: Path, env: dict[str, str]):
    return run_bounded_git_command(
        command,
        cwd=cwd,
        env=env,
        limits=GitCommandLimits(
            timeout_seconds=5,
            max_output_bytes=65_536,
            max_repository_bytes=MIN_TRANSFER_BYTES,
        ),
    )


@pytest.mark.parametrize("value", [MIN_TRANSFER_BYTES - 1, MAX_TRANSFER_BYTES + 1])
def test_git_transfer_config_rejects_out_of_range_values(value: int) -> None:
    with pytest.raises(ValidationError):
        _settings(GIT_MAX_TRANSFER_BYTES=value)


def test_git_transfer_config_accepts_boundaries_and_examples_are_documented() -> None:
    assert _settings(GIT_MAX_TRANSFER_BYTES=MIN_TRANSFER_BYTES).GIT_MAX_TRANSFER_BYTES == MIN_TRANSFER_BYTES
    assert _settings(GIT_MAX_TRANSFER_BYTES=MAX_TRANSFER_BYTES).GIT_MAX_TRANSFER_BYTES == MAX_TRANSFER_BYTES
    for relative_path in (".env.example", "infra/docker/.env.example"):
        content = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "GIT_MAX_TRANSFER_BYTES=536870912" in content


def test_git_transfer_config_loads_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("GIT_MAX_TRANSFER_BYTES", "2097152")

    assert _settings().GIT_MAX_TRANSFER_BYTES == 2_097_152
