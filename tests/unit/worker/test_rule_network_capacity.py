"""Rule egress connection-capacity and shutdown regressions."""

from __future__ import annotations

import socket
import socketserver
import tempfile
import threading

import pytest
from antcode_worker.engine.rule_egress_bridge import unix_proxy_bridge

_THREAD_JOIN_SECONDS = 5
# Measured reject-to-EOF latency under 12x CPU oversubscription: p99 3.7ms, max 6.4ms.
_REJECT_EOF_TIMEOUT_SECONDS = 1


class _EchoHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        data = self.request.recv(1024)
        self.request.sendall(data)


def _start_echo_server() -> tuple[socketserver.ThreadingTCPServer, threading.Thread]:
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _EchoHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _stop_server(server: socketserver.BaseServer, thread: threading.Thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=_THREAD_JOIN_SECONDS)


def test_unix_bridge_rejects_connections_above_task_limit() -> None:
    upstream, upstream_thread = _start_echo_server()
    first = None
    try:
        upstream_port = upstream.server_address[1]
        with tempfile.TemporaryDirectory(prefix="ac-rule-", dir="/tmp") as work_dir:
            with unix_proxy_bridge(
                f"http://127.0.0.1:{upstream_port}",
                work_dir,
                max_connections=1,
                max_duration_seconds=60,
            ) as socket_path:
                first = _open_active_connection(socket_path)
                _assert_second_connection_closed(socket_path)
    finally:
        if first is not None:
            first.close()
        _stop_server(upstream, upstream_thread)


def _open_active_connection(socket_path: str) -> socket.socket:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.connect(socket_path)
    client.sendall(b"first")
    assert client.recv(5) == b"first"
    return client


def _assert_second_connection_closed(socket_path: str) -> None:
    """A connection past the limit is accepted then closed unserviced, so it only reads EOF.

    The probe must not write. A write races the reject-side close and then reports EPIPE or
    ENOTCONN depending on kernel scheduling, neither of which says anything about the gate.
    Reading is race-free: EOF proves the reject, and payload or timeout proves a regression.
    """
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client.settimeout(_REJECT_EOF_TIMEOUT_SECONDS)
        client.connect(socket_path)
        assert client.recv(1) == b""
    finally:
        client.close()


def test_unix_bridge_shutdown_closes_active_connection() -> None:
    upstream, upstream_thread = _start_echo_server()
    client = None
    try:
        upstream_port = upstream.server_address[1]
        with tempfile.TemporaryDirectory(prefix="ac-rule-", dir="/tmp") as work_dir:
            with unix_proxy_bridge(
                f"http://127.0.0.1:{upstream_port}",
                work_dir,
                max_connections=1,
                max_duration_seconds=60,
            ) as socket_path:
                client = _open_active_connection(socket_path)
            assert client.recv(1) == b""
    finally:
        if client is not None:
            client.close()
        _stop_server(upstream, upstream_thread)


def test_unix_bridge_rejects_invalid_connection_limit() -> None:
    with tempfile.TemporaryDirectory(prefix="ac-rule-", dir="/tmp") as work_dir:
        upstream, upstream_thread = _start_echo_server()
        try:
            upstream_port = upstream.server_address[1]
            with pytest.raises(ValueError, match="正整数"):
                with unix_proxy_bridge(
                    f"http://127.0.0.1:{upstream_port}",
                    work_dir,
                    max_connections=0,
                    max_duration_seconds=60,
                ):
                    pass
        finally:
            _stop_server(upstream, upstream_thread)
