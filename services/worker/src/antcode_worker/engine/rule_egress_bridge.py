"""Host-side Unix bridge for a Rule task's isolated network namespace."""

from __future__ import annotations

import contextlib
import os
import secrets
import shutil
import socket
import socketserver
import stat
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

from antcode_core.common.bounded_threading_server import (
    BoundedThreadingMixIn,
    stop_bounded_server,
)

from antcode_worker.config import DATA_ROOT
from antcode_worker.executor.rule_network_io import (
    RULE_BRIDGE_CONNECT_TIMEOUT_SECONDS,
    RULE_BRIDGE_IDLE_TIMEOUT_SECONDS,
    relay_bidirectional_bounded,
)

_BRIDGE_THREAD_JOIN_SECONDS = 15
_PRIVATE_DIRECTORY_MODE = 0o700
_PRIVATE_SOCKET_MODE = 0o600
# Worker needs write+traverse to mint capabilities, but neither Worker payloads nor
# sibling tasks may enumerate active socket names. bwrap's read-only root also
# prevents payloads from creating entries despite the owner write bit.
_PRIVATE_ROOT_MODE = 0o300
_BRIDGE_TOKEN_BYTES = 12
_BRIDGE_ROOT = DATA_ROOT / "egress"


@dataclass(frozen=True)
class _UnixBridgeConfig:
    socket_path: Path
    upstream: tuple[str, int]
    max_connections: int
    max_duration_seconds: int


class _UnixBridgeServer(BoundedThreadingMixIn, socketserver.UnixStreamServer):
    request_thread_name = "antcode-rule-unix-connection"

    def __init__(self, config: _UnixBridgeConfig) -> None:
        self.upstream = config.upstream
        self.max_duration_seconds = config.max_duration_seconds
        self.configure_connection_limit(config.max_connections)
        super().__init__(str(config.socket_path), _UnixBridgeHandler)


class _UnixBridgeHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        server = cast(_UnixBridgeServer, self.server)
        client = cast(socket.socket, self.request)
        client.settimeout(RULE_BRIDGE_IDLE_TIMEOUT_SECONDS)
        upstream = socket.create_connection(
            server.upstream,
            timeout=RULE_BRIDGE_CONNECT_TIMEOUT_SECONDS,
        )
        with upstream:
            upstream.settimeout(RULE_BRIDGE_IDLE_TIMEOUT_SECONDS)
            relay_bidirectional_bounded(client, upstream, max_duration_seconds=server.max_duration_seconds)


def _upstream_address(proxy_url: str) -> tuple[str, int]:
    parsed = urlsplit(proxy_url)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.port is None:
        raise RuntimeError("Rule 受限代理必须是 Worker loopback HTTP 地址")
    return parsed.hostname, parsed.port


def _create_private_socket_dir(work_dir: Path) -> Path:
    if not work_dir.is_dir():
        raise RuntimeError(f"Rule 工作目录不存在: {work_dir}")
    _ensure_bridge_root()
    socket_dir = _BRIDGE_ROOT / f".e-{secrets.token_hex(_BRIDGE_TOKEN_BYTES)}"
    socket_dir.mkdir(mode=_PRIVATE_DIRECTORY_MODE)
    return socket_dir


def _ensure_bridge_root() -> None:
    _BRIDGE_ROOT.mkdir(parents=True, mode=_PRIVATE_ROOT_MODE, exist_ok=True)
    root_stat = _BRIDGE_ROOT.lstat()
    if _BRIDGE_ROOT.is_symlink() or not stat.S_ISDIR(root_stat.st_mode):
        raise RuntimeError("Rule egress bridge 根路径必须是真实目录")
    if root_stat.st_uid != os.geteuid():
        raise RuntimeError("Rule egress bridge 根路径必须由 Worker 运行用户持有")
    if stat.S_IMODE(root_stat.st_mode) != _PRIVATE_ROOT_MODE:
        _BRIDGE_ROOT.chmod(_PRIVATE_ROOT_MODE)


def _assert_private_socket(socket_path: Path) -> None:
    directory = socket_path.parent
    directory_stat = directory.lstat()
    socket_stat = socket_path.lstat()
    if directory.is_symlink() or not stat.S_ISDIR(directory_stat.st_mode):
        raise RuntimeError("Rule egress bridge 目录必须是真实目录")
    if socket_path.is_symlink() or not stat.S_ISSOCK(socket_stat.st_mode):
        raise RuntimeError("Rule egress bridge 必须是真实 Unix socket")
    if directory_stat.st_uid != os.geteuid() or socket_stat.st_uid != os.geteuid():
        raise RuntimeError("Rule egress bridge 必须由 Worker 运行用户持有")
    if stat.S_IMODE(directory_stat.st_mode) != _PRIVATE_DIRECTORY_MODE:
        raise RuntimeError("Rule egress bridge 目录权限必须是 0700")
    if stat.S_IMODE(socket_stat.st_mode) != _PRIVATE_SOCKET_MODE:
        raise RuntimeError("Rule egress bridge socket 权限必须是 0600")


@contextlib.contextmanager
def unix_proxy_bridge(
    proxy_url: str,
    work_dir: str,
    *,
    max_connections: int,
    max_duration_seconds: int,
) -> Iterator[str]:
    """Expose one private Unix socket that relays only to the safe proxy."""
    socket_dir = _create_private_socket_dir(Path(work_dir))
    socket_path = socket_dir / "p.sock"
    server: _UnixBridgeServer | None = None
    thread: threading.Thread | None = None
    try:
        config = _UnixBridgeConfig(
            socket_path=socket_path,
            upstream=_upstream_address(proxy_url),
            max_connections=max_connections,
            max_duration_seconds=max_duration_seconds,
        )
        server = _UnixBridgeServer(config)
        socket_path.chmod(_PRIVATE_SOCKET_MODE)
        _assert_private_socket(socket_path)
        thread = threading.Thread(
            target=server.serve_forever,
            kwargs={"poll_interval": 0.05},
            name="antcode-rule-unix-bridge",
        )
        thread.start()
        yield str(socket_path)
    finally:
        try:
            if server is not None:
                if thread is None:
                    server.server_close()
                else:
                    stop_bounded_server(server, thread, timeout=_BRIDGE_THREAD_JOIN_SECONDS)
        finally:
            shutil.rmtree(socket_dir)


__all__ = ["unix_proxy_bridge"]
