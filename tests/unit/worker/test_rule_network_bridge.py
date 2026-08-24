"""Rule egress bridge transport and filesystem security contract."""

from __future__ import annotations

import http.client
import os
import shutil
import socket
import socketserver
import sys
import tempfile
import threading
from pathlib import Path

import pytest
from antcode_worker.domain.models import ExecPlan
from antcode_worker.engine.rule_egress import rule_egress_plan
from antcode_worker.engine.rule_egress_bridge import unix_proxy_bridge
from antcode_worker.executor.rule_network_policy import bridge_mount_args
from antcode_worker.executor.rule_network_relay import _LoopbackRelayServer
from antcode_worker.executor.rule_policy import (
    RULE_EGRESS_CONNECTION_LIMIT_CONFIG,
    RULE_EGRESS_LOOPBACK_PORT,
    RULE_EGRESS_SOCKET_CONFIG,
)
from antcode_worker.executor.sandbox import BasicSandbox, SandboxConfig
from antcode_worker.rule_egress_limits import RuleEgressLimits

_THREAD_JOIN_SECONDS = 5
_BWRAP_PROBE_TIMEOUT_SECONDS = 15
_HTTP_OK = 200
_MAX_RULE_CONNECTIONS = 4
_NAMESPACE_PROBE = """
import socket
import sys

host_port = int(sys.argv[1])
try:
    socket.create_connection(("127.0.0.1", host_port), timeout=0.2)
except OSError:
    pass
else:
    raise SystemExit("isolated namespace reached Worker loopback directly")
with socket.create_connection(("127.0.0.1", int(sys.argv[2])), timeout=2) as client:
    client.sendall(b"namespace-ok")
    if client.recv(1024) != b"namespace-ok":
        raise SystemExit("bridge round trip failed")
"""


class _EchoHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        data = self.request.recv(1024)
        self.request.sendall(data)


class _HttpHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        self.request.recv(4096)
        self.request.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 7\r\nConnection: close\r\n\r\nhttp-ok")


def _start_echo_server() -> tuple[socketserver.ThreadingTCPServer, threading.Thread]:
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _EchoHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _start_http_server() -> tuple[socketserver.ThreadingTCPServer, threading.Thread]:
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _HttpHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _stop_server(server: socketserver.BaseServer, thread: threading.Thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=_THREAD_JOIN_SECONDS)


def test_loopback_relay_round_trips_through_private_unix_bridge() -> None:
    upstream, upstream_thread = _start_echo_server()
    relay = None
    relay_thread = None
    try:
        upstream_port = upstream.server_address[1]
        with tempfile.TemporaryDirectory(prefix="ac-rule-", dir="/tmp") as work_dir:
            with unix_proxy_bridge(
                f"http://127.0.0.1:{upstream_port}",
                work_dir,
                max_connections=_MAX_RULE_CONNECTIONS,
                max_duration_seconds=60,
            ) as socket_path:
                relay = _LoopbackRelayServer(Path(socket_path), _MAX_RULE_CONNECTIONS, 60)
                relay_thread = threading.Thread(target=relay.serve_forever, daemon=True)
                relay_thread.start()
                with socket.create_connection(("127.0.0.1", RULE_EGRESS_LOOPBACK_PORT)) as client:
                    client.sendall(b"bridge-ok")
                    assert client.recv(1024) == b"bridge-ok"
    finally:
        if relay is not None and relay_thread is not None:
            _stop_server(relay, relay_thread)
        _stop_server(upstream, upstream_thread)


def test_bridge_mount_rejects_group_readable_socket() -> None:
    with tempfile.TemporaryDirectory(prefix="ac-rule-", dir="/tmp") as work_dir:
        directory = Path(work_dir)
        directory.chmod(0o700)
        socket_path = directory / "p.sock"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(socket_path))
        os.chmod(socket_path, 0o640)
        try:
            with pytest.raises(RuntimeError, match="0600"):
                bridge_mount_args({RULE_EGRESS_SOCKET_CONFIG: str(socket_path)})
        finally:
            listener.close()


def test_bridge_mount_rejects_symlink_socket() -> None:
    with tempfile.TemporaryDirectory(prefix="ac-rule-", dir="/tmp") as work_dir:
        directory = Path(work_dir)
        directory.chmod(0o700)
        target = directory / "target.sock"
        link = directory / "p.sock"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(target))
        os.chmod(target, 0o600)
        link.symlink_to(target)
        try:
            with pytest.raises(RuntimeError, match="非符号链接"):
                bridge_mount_args({RULE_EGRESS_SOCKET_CONFIG: str(link)})
        finally:
            listener.close()


def test_bridge_mount_rejects_wrong_owner(monkeypatch) -> None:
    with tempfile.TemporaryDirectory(prefix="ac-rule-", dir="/tmp") as work_dir:
        socket_path = Path(work_dir) / "p.sock"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(socket_path))
        os.chmod(socket_path, 0o600)
        wrong_owner = os.geteuid() + 1
        monkeypatch.setattr("antcode_worker.executor.rule_network_policy.os.geteuid", lambda: wrong_owner)
        try:
            with pytest.raises(RuntimeError, match="运行用户"):
                bridge_mount_args({RULE_EGRESS_SOCKET_CONFIG: str(socket_path)})
        finally:
            listener.close()


def test_bridge_mount_rejects_non_socket_node() -> None:
    with tempfile.TemporaryDirectory(prefix="ac-rule-", dir="/tmp") as work_dir:
        path = Path(work_dir) / "p.sock"
        path.touch(mode=0o600)

        with pytest.raises(RuntimeError, match="类型无效"):
            bridge_mount_args({RULE_EGRESS_SOCKET_CONFIG: str(path)})


def test_bridge_mount_rejects_non_private_directory() -> None:
    with tempfile.TemporaryDirectory(prefix="ac-rule-", dir="/tmp") as work_dir:
        directory = Path(work_dir)
        socket_path = directory / "p.sock"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(socket_path))
        os.chmod(socket_path, 0o600)
        directory.chmod(0o750)
        try:
            with pytest.raises(RuntimeError, match="0700"):
                bridge_mount_args({RULE_EGRESS_SOCKET_CONFIG: str(socket_path)})
        finally:
            listener.close()


def test_bridge_cleanup_runs_when_upstream_configuration_is_invalid() -> None:
    with tempfile.TemporaryDirectory(prefix="ac-rule-", dir="/tmp") as work_dir:
        with pytest.raises(RuntimeError, match="loopback"):
            with unix_proxy_bridge(
                "http://192.0.2.1:8080",
                work_dir,
                max_connections=_MAX_RULE_CONNECTIONS,
                max_duration_seconds=60,
            ):
                pass

        assert list(Path(work_dir).iterdir()) == []


def test_full_rule_bridge_relays_http_and_connect(monkeypatch) -> None:
    http_server, http_thread = _start_http_server()
    echo_server, echo_thread = _start_echo_server()
    try:
        _run_http_and_connect_probes(monkeypatch, http_server, echo_server)
    finally:
        _stop_server(http_server, http_thread)
        _stop_server(echo_server, echo_thread)


def _run_http_and_connect_probes(monkeypatch, http_server, echo_server) -> None:
    monkeypatch.setattr(
        "antcode_core.application.services.projects.pinned_http_proxy.resolve_host_addresses",
        _resolve_public_target,
    )
    with tempfile.TemporaryDirectory(prefix="ac-rule-", dir="/tmp") as work_dir:
        spool_path = Path(work_dir) / "spool.jsonl"
        plan = ExecPlan(
            command=sys.executable,
            plugin_name="rule",
            env={"ANTCODE_SPIDER_SPOOL_PATH": str(spool_path)},
        )
        limits = RuleEgressLimits(
            max_connections=_MAX_RULE_CONNECTIONS,
            max_bytes=1024 * 1024,
            max_duration_seconds=60,
        )
        with rule_egress_plan(plan, limits, default_max_processes=_MAX_RULE_CONNECTIONS) as secured:
            relay = _LoopbackRelayServer(
                Path(secured.sandbox_config[RULE_EGRESS_SOCKET_CONFIG]),
                _MAX_RULE_CONNECTIONS,
                60,
            )
            relay_thread = threading.Thread(target=relay.serve_forever, daemon=True)
            relay_thread.start()
            try:
                _assert_http_proxy(http_server.server_address[1])
                _assert_connect_proxy(echo_server.server_address[1])
                _assert_private_target_blocked(http_server.server_address[1])
            finally:
                _stop_server(relay, relay_thread)


def _resolve_public_target(host: str) -> list[str]:
    if host != "public.example":
        raise ValueError("blocked")
    return ["127.0.0.1"]


def _assert_http_proxy(port: int) -> None:
    connection = http.client.HTTPConnection("127.0.0.1", RULE_EGRESS_LOOPBACK_PORT, timeout=2)
    connection.request("GET", f"http://public.example:{port}/health")
    response = connection.getresponse()
    assert response.status == _HTTP_OK
    assert response.read() == b"http-ok"
    connection.close()


def _assert_connect_proxy(port: int) -> None:
    with socket.create_connection(("127.0.0.1", RULE_EGRESS_LOOPBACK_PORT), timeout=2) as client:
        client.sendall(f"CONNECT public.example:{port} HTTP/1.1\r\nHost: public.example\r\n\r\n".encode())
        assert b"200 Connection Established" in client.recv(4096)
        client.sendall(b"connect-ok")
        assert client.recv(1024) == b"connect-ok"


def _assert_private_target_blocked(port: int) -> None:
    with socket.create_connection(("127.0.0.1", RULE_EGRESS_LOOPBACK_PORT), timeout=2) as client:
        client.sendall(f"GET http://127.0.0.1:{port}/ HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n".encode())
        try:
            response = client.recv(1024)
        except ConnectionResetError:
            response = b""
        assert response == b""


@pytest.mark.skipif(sys.platform != "linux" or shutil.which("bwrap") is None, reason="需要 Linux bubblewrap")
@pytest.mark.asyncio
async def test_bwrap_network_namespace_reaches_only_unix_bridge() -> None:
    upstream, upstream_thread = _start_echo_server()
    try:
        upstream_port = upstream.server_address[1]
        with tempfile.TemporaryDirectory(prefix="ac-rule-", dir="/tmp") as work_dir:
            with unix_proxy_bridge(
                f"http://127.0.0.1:{upstream_port}",
                work_dir,
                max_connections=_MAX_RULE_CONNECTIONS,
                max_duration_seconds=60,
            ) as socket_path:
                sandbox = BasicSandbox(
                    SandboxConfig(network_isolated=True, sandbox_command=[shutil.which("bwrap") or ""])
                )
                plan = ExecPlan(
                    command=sys.executable,
                    plugin_name="rule",
                    sandbox_config={
                        RULE_EGRESS_SOCKET_CONFIG: socket_path,
                        RULE_EGRESS_CONNECTION_LIMIT_CONFIG: _MAX_RULE_CONNECTIONS,
                    },
                )
                context = {**await sandbox.prepare(plan, work_dir), "tmpfs_size_mb": 0}
                command = sandbox.wrap_command(
                    [sys.executable, "-c", _NAMESPACE_PROBE, str(upstream_port), str(RULE_EGRESS_LOOPBACK_PORT)],
                    context,
                )
                __import__("subprocess").run(command, check=True, timeout=_BWRAP_PROBE_TIMEOUT_SECONDS)
    finally:
        _stop_server(upstream, upstream_thread)
