"""Connection-bounded socketserver mixin with deterministic thread cleanup."""

from __future__ import annotations

import contextlib
import socket
import socketserver
import threading
import time
from typing import Any, Protocol, cast

SocketRequest = socket.socket | tuple[bytes, socket.socket]


class _ServerLifecycle(Protocol):
    def shutdown(self) -> None: ...

    def server_close(self) -> None: ...


class BoundedThreadingMixIn(socketserver.ThreadingMixIn):
    """Start at most ``max_connections`` request threads at once."""

    daemon_threads = False
    block_on_close = False
    request_thread_name = "antcode-bounded-request"

    def configure_connection_limit(self, max_connections: int) -> None:
        if type(max_connections) is not int or max_connections <= 0:
            raise ValueError("max_connections 必须是正整数")
        self.max_connections = max_connections
        self._connection_slots = threading.BoundedSemaphore(max_connections)
        self._request_state_lock = threading.Lock()
        self._active_requests: set[SocketRequest] = set()
        self._request_threads: set[threading.Thread] = set()
        self._stopping = False

    def process_request(self, request: SocketRequest, client_address: Any) -> None:
        if not self._admit_request(request):
            self._reject_capacity_exceeded(request)
            self._shutdown_request(request)
            return
        thread = threading.Thread(
            target=self._process_bounded_request,
            args=(request, client_address),
            name=self.request_thread_name,
        )
        with self._request_state_lock:
            self._request_threads.add(thread)
        try:
            thread.start()
        except BaseException:
            with self._request_state_lock:
                self._active_requests.discard(request)
                self._request_threads.discard(thread)
            self._connection_slots.release()
            self._shutdown_request(request)
            raise

    def _admit_request(self, request: SocketRequest) -> bool:
        if not self._connection_slots.acquire(blocking=False):
            return False
        with self._request_state_lock:
            if self._stopping:
                self._connection_slots.release()
                return False
            self._reap_request_threads()
            self._active_requests.add(request)
        return True

    def _process_bounded_request(self, request: SocketRequest, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._discard_request(request)

    def _discard_request(self, request: SocketRequest) -> None:
        with self._request_state_lock:
            self._active_requests.discard(request)
        self._connection_slots.release()

    def _reap_request_threads(self) -> None:
        self._request_threads = {thread for thread in self._request_threads if thread.is_alive()}

    def _reject_capacity_exceeded(self, request: SocketRequest) -> None:
        """Subclasses may send a protocol-specific overload response before close."""

    def _shutdown_request(self, request: SocketRequest) -> None:
        cast(Any, self).shutdown_request(request)

    def close_active_requests(self) -> None:
        with self._request_state_lock:
            self._stopping = True
            requests = tuple(self._active_requests)
        for request in requests:
            request_socket = request[1] if isinstance(request, tuple) else request
            with contextlib.suppress(OSError):
                request_socket.shutdown(socket.SHUT_RDWR)

    def join_request_threads(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        with self._request_state_lock:
            threads = tuple(self._request_threads)
        for thread in threads:
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
        alive = tuple(thread for thread in threads if thread.is_alive())
        if alive:
            raise RuntimeError(f"有 {len(alive)} 个代理连接线程未在期限内停止")
        with self._request_state_lock:
            self._reap_request_threads()

    @property
    def active_connection_count(self) -> int:
        with self._request_state_lock:
            return len(self._active_requests)


def stop_bounded_server(
    server: BoundedThreadingMixIn,
    server_thread: threading.Thread,
    *,
    timeout: float,
) -> None:
    """Stop accepting, interrupt live sockets, and join every server thread."""
    lifecycle = cast(_ServerLifecycle, server)
    if server_thread.ident is None:
        lifecycle.server_close()
        return
    lifecycle.shutdown()
    server.close_active_requests()
    lifecycle.server_close()
    server_thread.join(timeout=timeout)
    if server_thread.is_alive():
        raise RuntimeError("代理监听线程未在期限内停止")
    server.join_request_threads(timeout)


__all__ = ["BoundedThreadingMixIn", "stop_bounded_server"]
