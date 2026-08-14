"""Workflow-scoped byte quota and pinned TCP relay for Git transports."""

from __future__ import annotations

import contextlib
import secrets
import selectors
import socket
import socketserver
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import cast

RELAY_BUFFER_BYTES = 64 * 1024
RELAY_CONNECT_TIMEOUT_SECONDS = 15
RELAY_POLL_SECONDS = 0.1


class GitNetworkLimitExceeded(ValueError):
    """Raised after a pinned transport exhausts its shared byte quota."""

    def __init__(self, max_bytes: int, label: str = "Git 网络传输") -> None:
        self.label = label
        super().__init__(f"{label}超过上限 {max_bytes} 字节")


class NetworkDurationLimitExceeded(TimeoutError):
    """Raised after a pinned transport exhausts its wall-clock budget."""

    def __init__(self, max_duration_seconds: float, label: str = "网络传输") -> None:
        self.label = label
        super().__init__(f"{label}超过总时长上限 {max_duration_seconds:g} 秒")


class DurationBudget:
    """Share one monotonic deadline across every connection in an operation."""

    def __init__(self, max_duration_seconds: float, *, label: str = "网络传输") -> None:
        if max_duration_seconds <= 0:
            raise ValueError("网络传输总时长上限必须为正数")
        self.max_duration_seconds = max_duration_seconds
        self.label = label
        self._deadline = time.monotonic() + max_duration_seconds
        self._exceeded = threading.Event()

    @property
    def exceeded(self) -> bool:
        return self._exceeded.is_set()

    def remaining_seconds(self) -> float:
        remaining = self._deadline - time.monotonic()
        if remaining > 0 and not self.exceeded:
            return remaining
        self._exceeded.set()
        raise NetworkDurationLimitExceeded(self.max_duration_seconds, self.label)

    def raise_if_exceeded(self) -> None:
        if self.exceeded or time.monotonic() >= self._deadline:
            self._exceeded.set()
            raise NetworkDurationLimitExceeded(self.max_duration_seconds, self.label)


class TransferBudget:
    """Atomically account bytes relayed across every connection in a workflow."""

    def __init__(self, max_bytes: int, *, label: str = "Git 网络传输") -> None:
        if max_bytes <= 0:
            raise ValueError("Git 网络传输上限必须为正整数")
        self.max_bytes = max_bytes
        self.label = label
        self._used_bytes = 0
        self._exceeded = threading.Event()
        self._lock = threading.Lock()

    @property
    def exceeded(self) -> bool:
        return self._exceeded.is_set()

    @property
    def used_bytes(self) -> int:
        with self._lock:
            return self._used_bytes

    def recv_size(self) -> int:
        with self._lock:
            remaining = self.max_bytes - self._used_bytes
        return min(RELAY_BUFFER_BYTES, max(remaining + 1, 1))

    def account(self, size: int) -> None:
        with self._lock:
            if self._exceeded.is_set() or self._used_bytes + size > self.max_bytes:
                self._exceeded.set()
                raise GitNetworkLimitExceeded(self.max_bytes, self.label)
            self._used_bytes += size

    def failure(self) -> Exception | None:
        if self.exceeded:
            return GitNetworkLimitExceeded(self.max_bytes, self.label)
        return None

    def raise_if_exceeded(self) -> None:
        failure = self.failure()
        if failure is not None:
            raise failure


def relay_bidirectional(
    left: socket.socket,
    right: socket.socket,
    *,
    budget: TransferBudget | None,
    duration_budget: DurationBudget | None = None,
) -> None:
    selector = selectors.DefaultSelector()
    selector.register(left, selectors.EVENT_READ, right)
    selector.register(right, selectors.EVENT_READ, left)
    with selector:
        while selector.get_map() and not (budget and budget.exceeded):
            remaining = duration_budget.remaining_seconds() if duration_budget is not None else RELAY_POLL_SECONDS
            events = selector.select(min(RELAY_POLL_SECONDS, remaining))
            for key, _event_mask in events:
                _relay_once(
                    selector,
                    cast(socket.socket, key.fileobj),
                    cast(socket.socket, key.data),
                    budget=budget,
                )


def send_accounted(destination: socket.socket, data: bytes, budget: TransferBudget | None) -> None:
    if budget is not None:
        budget.account(len(data))
    destination.sendall(data)


def _relay_once(
    selector: selectors.BaseSelector,
    source: socket.socket,
    destination: socket.socket,
    *,
    budget: TransferBudget | None,
) -> None:
    size = budget.recv_size() if budget is not None else RELAY_BUFFER_BYTES
    data = source.recv(size)
    if not data:
        _close_read_side(selector, source, destination)
        return
    send_accounted(destination, data, budget)


def _close_read_side(
    selector: selectors.BaseSelector,
    source: socket.socket,
    destination: socket.socket,
) -> None:
    with contextlib.suppress(Exception):
        selector.unregister(source)
    with contextlib.suppress(OSError):
        destination.shutdown(socket.SHUT_WR)


@dataclass(frozen=True)
class RelayEndpoint:
    host: str
    port: int
    token: str


@dataclass(frozen=True)
class _RelayConfig:
    upstream: tuple[str, int]
    budget: TransferBudget
    token: str


class _PinnedRelayServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, config: _RelayConfig) -> None:
        self.upstream = config.upstream
        self.budget = config.budget
        self.token = config.token
        super().__init__(("127.0.0.1", 0), _PinnedRelayHandler)


class _PinnedRelayHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        server = cast(_PinnedRelayServer, self.server)
        client = cast(socket.socket, self.request)
        client.settimeout(RELAY_CONNECT_TIMEOUT_SECONDS)
        if not _authenticate(client, server.token):
            return
        if server.budget.exceeded:
            return
        try:
            upstream = socket.create_connection(server.upstream, timeout=RELAY_CONNECT_TIMEOUT_SECONDS)
            with upstream:
                relay_bidirectional(client, upstream, budget=server.budget)
        except GitNetworkLimitExceeded:
            return


def _authenticate(client: socket.socket, expected: str) -> bool:
    received = bytearray()
    expected_size = len(expected.encode("ascii")) + 1
    while len(received) < expected_size:
        chunk = client.recv(expected_size - len(received))
        if not chunk:
            return False
        received.extend(chunk)
    return secrets.compare_digest(bytes(received), f"{expected}\n".encode("ascii"))


@contextlib.contextmanager
def pinned_tcp_relay(
    address: str,
    port: int,
    *,
    budget: TransferBudget,
) -> Iterator[RelayEndpoint]:
    token = secrets.token_urlsafe(32)
    config = _RelayConfig(upstream=(address, port), budget=budget, token=token)
    server = _PinnedRelayServer(config)
    thread = threading.Thread(target=server.serve_forever, name="antcode-pinned-git-relay", daemon=True)
    thread.start()
    host, relay_port = cast(tuple[str, int], server.server_address)
    try:
        yield RelayEndpoint(host=host, port=relay_port, token=token)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=RELAY_CONNECT_TIMEOUT_SECONDS)
        budget.raise_if_exceeded()


__all__ = [
    "DurationBudget",
    "GitNetworkLimitExceeded",
    "NetworkDurationLimitExceeded",
    "RelayEndpoint",
    "TransferBudget",
    "pinned_tcp_relay",
    "relay_bidirectional",
    "send_accounted",
]
