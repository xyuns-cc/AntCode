"""Process-local HTTP proxy that connects only to a prevalidated address."""

from __future__ import annotations

import contextlib
import socket
import socketserver
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import cast
from urllib.parse import urlsplit, urlunsplit

from antcode_core.application.services.projects.git_transfer_quota import (
    GitNetworkLimitExceeded,
    TransferBudget,
    relay_bidirectional,
    send_accounted,
)
from antcode_core.application.services.projects.git_url_security import (
    resolve_host_addresses,
)

MAX_PROXY_HEADER_BYTES = 64 * 1024
PROXY_CONNECT_TIMEOUT_SECONDS = 15
PROXY_IO_TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class PinnedProxyTarget:
    host: str
    port: int
    address: str


TargetResolver = Callable[[str, str], PinnedProxyTarget]


class _PinnedProxyServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, resolver: TargetResolver, budget: TransferBudget | None) -> None:
        self.resolve_target = resolver
        self.transfer_budget = budget
        super().__init__(("127.0.0.1", 0), _PinnedProxyHandler)


class _PinnedProxyHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        client = self.request
        client.settimeout(PROXY_IO_TIMEOUT_SECONDS)
        header, trailing = _read_header(client)
        method, raw_target, version = _parse_request_line(header)
        server = cast(_PinnedProxyServer, self.server)
        target_config = server.resolve_target(method, raw_target)
        upstream = socket.create_connection(
            (target_config.address, target_config.port),
            timeout=PROXY_CONNECT_TIMEOUT_SECONDS,
        )
        with upstream:
            upstream.settimeout(PROXY_IO_TIMEOUT_SECONDS)
            try:
                if method == "CONNECT":
                    client.sendall(f"{version} 200 Connection Established\r\n\r\n".encode())
                else:
                    request = _origin_request(header, raw_target, target_config) + trailing
                    send_accounted(upstream, request, server.transfer_budget)
                relay_bidirectional(client, upstream, budget=server.transfer_budget)
            except GitNetworkLimitExceeded:
                return


def _read_header(client: socket.socket) -> tuple[bytes, bytes]:
    data = bytearray()
    while b"\r\n\r\n" not in data:
        chunk = client.recv(4096)
        if not chunk:
            raise ConnectionError("proxy client closed before sending headers")
        data.extend(chunk)
        if len(data) > MAX_PROXY_HEADER_BYTES:
            raise ValueError("proxy request headers too large")
    marker = data.index(b"\r\n\r\n") + 4
    return bytes(data[:marker]), bytes(data[marker:])


def _parse_request_line(header: bytes) -> tuple[str, str, str]:
    first_line = header.split(b"\r\n", 1)[0].decode("ascii", errors="strict")
    parts = first_line.split(" ")
    if len(parts) != 3:
        raise ValueError("invalid proxy request line")
    return parts[0].upper(), parts[1], parts[2]


def _assert_allowed_target(method: str, raw_target: str, expected: PinnedProxyTarget) -> None:
    host, port = _target_host_port(method, raw_target)
    if host != expected.host.lower() or port != expected.port:
        raise PermissionError(f"proxy target is not pinned: {host}:{port}")


def _target_host_port(method: str, raw_target: str) -> tuple[str, int]:
    parsed = urlsplit(f"//{raw_target}") if method == "CONNECT" else urlsplit(raw_target)
    if method != "CONNECT" and parsed.scheme not in {"http", "https"}:
        raise PermissionError(f"proxy scheme is not allowed: {parsed.scheme!r}")
    if parsed.username or parsed.password:
        raise PermissionError("proxy target must not contain URL credentials")
    host = (parsed.hostname or "").lower()
    if not host:
        raise PermissionError("proxy target host is empty")
    port = parsed.port or (443 if parsed.scheme == "https" or method == "CONNECT" else 80)
    return host, port


def _origin_request(header: bytes, raw_target: str, target: PinnedProxyTarget) -> bytes:
    parsed = urlsplit(raw_target)
    origin_target = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    lines = header.decode("iso-8859-1").split("\r\n")
    method, _target, version = lines[0].split(" ")
    blocked_headers = _blocked_forward_headers(lines[1:])
    forwarded = [
        f"{method} {origin_target} {version}",
        f"Host: {_host_header(target, parsed.scheme)}",
    ]
    forwarded.extend(
        line for line in lines[1:] if line and line.split(":", 1)[0].strip().lower() not in blocked_headers
    )
    forwarded.extend(["Connection: close", "", ""])
    return "\r\n".join(forwarded).encode("iso-8859-1")


def _blocked_forward_headers(lines: list[str]) -> set[str]:
    blocked = {"connection", "host", "proxy-authorization", "proxy-connection"}
    for line in lines:
        name, separator, value = line.partition(":")
        if separator and name.strip().lower() == "connection":
            blocked.update(item.strip().lower() for item in value.split(",") if item.strip())
    return blocked


def _host_header(target: PinnedProxyTarget, scheme: str) -> str:
    host = f"[{target.host}]" if ":" in target.host else target.host
    default_port = 443 if scheme == "https" else 80
    return host if target.port == default_port else f"{host}:{target.port}"


@contextlib.contextmanager
def pinned_http_proxy(
    target: PinnedProxyTarget,
    *,
    budget: TransferBudget | None = None,
) -> Iterator[str]:
    def resolve(method: str, raw_target: str) -> PinnedProxyTarget:
        _assert_allowed_target(method, raw_target, target)
        return target

    with _run_proxy(resolve, budget) as proxy_url:
        try:
            yield proxy_url
        finally:
            if budget is not None:
                budget.raise_if_exceeded()


@contextlib.contextmanager
def restricted_http_proxy() -> Iterator[str]:
    """Proxy HTTP(S) only to addresses validated as publicly routable."""

    def resolve(method: str, raw_target: str) -> PinnedProxyTarget:
        host, port = _target_host_port(method, raw_target)
        addresses = resolve_host_addresses(host)
        return PinnedProxyTarget(host=host, port=port, address=addresses[0])

    with _run_proxy(resolve, None) as proxy_url:
        yield proxy_url


@contextlib.contextmanager
def _run_proxy(resolver: TargetResolver, budget: TransferBudget | None) -> Iterator[str]:
    server = _PinnedProxyServer(resolver, budget)
    thread = threading.Thread(target=server.serve_forever, name="antcode-pinned-http-proxy", daemon=True)
    thread.start()
    host, port = cast(tuple[str, int], server.server_address)
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=PROXY_CONNECT_TIMEOUT_SECONDS)


__all__ = ["PinnedProxyTarget", "pinned_http_proxy", "restricted_http_proxy"]
