"""Trusted loopback relay launched inside a Rule task network namespace."""

from __future__ import annotations

import os
import socket
import socketserver
import stat
import subprocess
import sys
import threading
from pathlib import Path
from typing import cast

from antcode_core.common.bounded_threading_server import (
    BoundedThreadingMixIn,
    stop_bounded_server,
)

from antcode_worker.executor.rule_network_io import (
    RULE_BRIDGE_CONNECT_TIMEOUT_SECONDS,
    RULE_BRIDGE_IDLE_TIMEOUT_SECONDS,
    relay_bidirectional_bounded,
)
from antcode_worker.executor.rule_policy import RULE_EGRESS_LOOPBACK_PORT

_SERVER_THREAD_JOIN_SECONDS = 5
_PRIVATE_DIRECTORY_MODE = 0o700
_PRIVATE_SOCKET_MODE = 0o600
_RELAY_ARG_COUNT_BEFORE_COMMAND = 8


class _LoopbackRelayServer(BoundedThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    request_thread_name = "antcode-rule-loopback-connection"

    def __init__(self, socket_path: Path, max_connections: int, max_duration_seconds: int) -> None:
        self.socket_path = socket_path
        self.max_duration_seconds = max_duration_seconds
        self.configure_connection_limit(max_connections)
        super().__init__(("127.0.0.1", RULE_EGRESS_LOOPBACK_PORT), _LoopbackRelayHandler)


class _LoopbackRelayHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        server = cast(_LoopbackRelayServer, self.server)
        client = cast(socket.socket, self.request)
        client.settimeout(RULE_BRIDGE_IDLE_TIMEOUT_SECONDS)
        upstream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        with upstream:
            upstream.settimeout(RULE_BRIDGE_CONNECT_TIMEOUT_SECONDS)
            upstream.connect(str(server.socket_path))
            upstream.settimeout(RULE_BRIDGE_IDLE_TIMEOUT_SECONDS)
            relay_bidirectional_bounded(client, upstream, max_duration_seconds=server.max_duration_seconds)


def _validate_socket(socket_path: Path) -> None:
    directory_stat = socket_path.parent.lstat()
    socket_stat = socket_path.lstat()
    if socket_path.parent.is_symlink() or not stat.S_ISDIR(directory_stat.st_mode):
        raise RuntimeError("Rule egress bridge 目录无效")
    if socket_path.is_symlink() or not stat.S_ISSOCK(socket_stat.st_mode):
        raise RuntimeError("Rule egress bridge socket 无效")
    if stat.S_IMODE(directory_stat.st_mode) != _PRIVATE_DIRECTORY_MODE:
        raise RuntimeError("Rule egress bridge 目录权限不是 0700")
    if stat.S_IMODE(socket_stat.st_mode) != _PRIVATE_SOCKET_MODE:
        raise RuntimeError("Rule egress bridge socket 权限不是 0600")
    if directory_stat.st_uid != os.geteuid() or socket_stat.st_uid != os.geteuid():
        raise RuntimeError("Rule egress bridge 必须由当前 namespace 用户持有")


def _parse_command(argv: list[str]) -> tuple[Path, int, int, list[str]]:
    valid_prefix = (
        argv[:1] == ["--socket"]
        and argv[2:3] == ["--max-connections"]
        and argv[4:5] == ["--max-duration"]
        and argv[6:7] == ["--"]
    )
    if len(argv) < _RELAY_ARG_COUNT_BEFORE_COMMAND or not valid_prefix:
        raise RuntimeError("Rule namespace relay 参数格式错误")
    try:
        max_connections = int(argv[3])
        max_duration_seconds = int(argv[5])
    except ValueError as exc:
        raise RuntimeError("Rule namespace relay 连接上限格式错误") from exc
    if max_connections <= 0:
        raise RuntimeError("Rule namespace relay 连接上限必须为正数")
    if max_duration_seconds <= 0:
        raise RuntimeError("Rule namespace relay 总时长上限必须为正数")
    command = argv[7:]
    if not command:
        raise RuntimeError("Rule namespace relay 缺少 payload 命令")
    return Path(argv[1]), max_connections, max_duration_seconds, command


def run(argv: list[str] | None = None) -> int:
    socket_path, max_connections, max_duration_seconds, command = _parse_command(
        list(sys.argv[1:] if argv is None else argv)
    )
    _validate_socket(socket_path)
    server = _LoopbackRelayServer(socket_path, max_connections, max_duration_seconds)
    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.05},
        name="antcode-rule-loopback-relay",
    )
    try:
        thread.start()
        return subprocess.run(command, check=False).returncode
    finally:
        stop_bounded_server(server, thread, timeout=_SERVER_THREAD_JOIN_SECONDS)


if __name__ == "__main__":
    raise SystemExit(run())
