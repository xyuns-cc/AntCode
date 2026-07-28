"""Pinned and byte-bounded Git transport environment construction."""

from __future__ import annotations

import contextlib
import os
import shlex
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass

from antcode_core.application.services.projects.git_credential_service import GitAuthConfig
from antcode_core.application.services.projects.git_transfer_quota import (
    RelayEndpoint,
    TransferBudget,
    pinned_tcp_relay,
)
from antcode_core.application.services.projects.git_url_security import ResolvedURL
from antcode_core.application.services.projects.pinned_http_proxy import (
    PinnedProxyTarget,
    pinned_http_proxy,
)

SSH_PROXY_MODULE = "antcode_core.application.services.projects.git_ssh_proxy"


@dataclass(frozen=True)
class GitTransportSession:
    safe_config_dir: str
    proxy_url: str | None = None
    ssh_command: str | None = None
    transfer_budget: TransferBudget | None = None

    def failure(self) -> Exception | None:
        if self.transfer_budget is None:
            return None
        return self.transfer_budget.failure()


def build_git_env(
    auth_config: GitAuthConfig | None,
    endpoint: ResolvedURL | None,
    transport: GitTransportSession,
) -> dict[str, str]:
    env = os.environ.copy()
    for name in tuple(env):
        if name.startswith("GIT_"):
            env.pop(name)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_ATTR_NOSYSTEM"] = "1"
    env["GIT_LFS_SKIP_SMUDGE"] = "1"
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env.pop(name, None)
    entries = _safe_git_config(transport.safe_config_dir)
    if auth_config is not None:
        entries.append(("http.extraHeader", auth_config.header_value))
    if endpoint and endpoint.scheme in {"http", "https"}:
        if transport is None or not transport.proxy_url:
            raise RuntimeError("HTTP(S) Git endpoint 缺少 pinned proxy")
        entries.append(("http.proxy", transport.proxy_url))
    if endpoint and endpoint.scheme == "ssh":
        if transport is None or not transport.ssh_command:
            raise RuntimeError("SSH Git endpoint 缺少 pinned relay")
        env["GIT_SSH_COMMAND"] = transport.ssh_command
    env["GIT_CONFIG_COUNT"] = str(len(entries))
    for index, (key, value) in enumerate(entries):
        env[f"GIT_CONFIG_KEY_{index}"] = key
        env[f"GIT_CONFIG_VALUE_{index}"] = value
    return env


def _safe_git_config(safe_config_dir: str) -> list[tuple[str, str]]:
    return [
        ("http.followRedirects", "false"),
        ("protocol.allow", "never"),
        ("protocol.http.allow", "always"),
        ("protocol.https.allow", "always"),
        ("protocol.ssh.allow", "always"),
        ("protocol.file.allow", "never"),
        ("protocol.ext.allow", "never"),
        ("submodule.recurse", "false"),
        ("fetch.recurseSubmodules", "false"),
        ("core.hooksPath", safe_config_dir),
        ("init.templateDir", safe_config_dir),
        ("filter.lfs.required", "false"),
    ]


@contextlib.contextmanager
def git_transport(
    endpoint: ResolvedURL | None,
    transfer_budget: TransferBudget,
) -> Iterator[GitTransportSession]:
    with tempfile.TemporaryDirectory(prefix="antcode-git-config-") as safe_dir:
        if endpoint is None:
            yield GitTransportSession(safe_config_dir=safe_dir)
            return
        if endpoint.scheme in {"http", "https"}:
            with _http_transport(endpoint, transfer_budget, safe_dir) as session:
                yield session
            return
        if endpoint.scheme == "ssh":
            with _ssh_transport(endpoint, transfer_budget, safe_dir) as session:
                yield session
            return
        raise ValueError(f"Git transport scheme 不受支持: {endpoint.scheme!r}")


@contextlib.contextmanager
def _http_transport(
    endpoint: ResolvedURL,
    budget: TransferBudget,
    safe_dir: str,
) -> Iterator[GitTransportSession]:
    target = PinnedProxyTarget(
        host=endpoint.host,
        port=endpoint.port,
        address=endpoint.primary_address,
    )
    with pinned_http_proxy(target, budget=budget) as proxy_url:
        yield GitTransportSession(safe_config_dir=safe_dir, proxy_url=proxy_url, transfer_budget=budget)


@contextlib.contextmanager
def _ssh_transport(
    endpoint: ResolvedURL,
    budget: TransferBudget,
    safe_dir: str,
) -> Iterator[GitTransportSession]:
    with pinned_tcp_relay(endpoint.primary_address, endpoint.port, budget=budget) as relay:
        try:
            yield GitTransportSession(
                safe_config_dir=safe_dir,
                ssh_command=pinned_ssh_command(endpoint, relay),
                transfer_budget=budget,
            )
        finally:
            budget.raise_if_exceeded()


def pinned_ssh_command(endpoint: ResolvedURL, relay: RelayEndpoint) -> str:
    proxy_command = _proxy_command(relay)
    options = [
        "ssh",
        "-F",
        os.devnull,
        "-o",
        f"Hostname={endpoint.primary_address}",
        "-o",
        f"HostKeyAlias={endpoint.host}",
        "-o",
        "CanonicalizeHostname=no",
        "-o",
        f"ProxyCommand={proxy_command}",
        "-o",
        "ProxyJump=none",
        "-o",
        "ProxyUseFdpass=no",
        "-o",
        "NoHostAuthenticationForProxyCommand=no",
        "-o",
        "PermitLocalCommand=no",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
    ]
    return shlex.join(options)


def _proxy_command(relay: RelayEndpoint) -> str:
    command = shlex.join(
        [
            sys.executable,
            "-m",
            SSH_PROXY_MODULE,
            str(relay.port),
            relay.token,
        ]
    )
    return command.replace("%", "%%")


__all__ = [
    "GitTransportSession",
    "build_git_env",
    "git_transport",
    "pinned_ssh_command",
]
