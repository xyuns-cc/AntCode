import socket
import socketserver
import threading
import time
from urllib.parse import urlsplit

import pytest
from antcode_core.application.services.projects import pinned_http_proxy as proxy_module
from antcode_core.application.services.projects.git_transfer_quota import (
    DurationBudget,
    GitNetworkLimitExceeded,
    NetworkDurationLimitExceeded,
    TransferBudget,
)
from antcode_core.application.services.projects.pinned_http_proxy import (
    PinnedProxyTarget,
    _assert_allowed_target,
    _origin_request,
    pinned_http_proxy,
    restricted_http_proxy,
)
from antcode_core.common.config import settings

_THREAD_JOIN_SECONDS = 5
_HTTP_SERVICE_UNAVAILABLE = b"HTTP/1.1 503 Service Unavailable"


class _EchoHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        while data := self.request.recv(4096):
            self.request.sendall(data)


class _SinkHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        while self.request.recv(4096):
            pass


def test_connect_tunnel_uses_pinned_address() -> None:
    origin = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _EchoHandler)
    origin_thread = threading.Thread(target=origin.serve_forever, daemon=True)
    origin_thread.start()
    origin_port = int(origin.server_address[1])
    target = PinnedProxyTarget(host="example.com", port=origin_port, address="127.0.0.1")

    try:
        with pinned_http_proxy(target) as proxy_url:
            proxy = urlsplit(proxy_url)
            with socket.create_connection((proxy.hostname, proxy.port), timeout=5) as client:
                request = f"CONNECT example.com:{origin_port} HTTP/1.1\r\nHost: example.com\r\n\r\n"
                client.sendall(request.encode())
                assert client.recv(4096).startswith(b"HTTP/1.1 200")
                client.sendall(b"pinned-connection")
                assert client.recv(4096) == b"pinned-connection"
    finally:
        origin.shutdown()
        origin.server_close()
        origin_thread.join(timeout=5)


def test_connect_tunnel_enforces_bidirectional_byte_limit() -> None:
    origin = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _EchoHandler)
    origin_thread = threading.Thread(target=origin.serve_forever, daemon=True)
    origin_thread.start()
    origin_port = int(origin.server_address[1])
    target = PinnedProxyTarget(host="example.com", port=origin_port, address="127.0.0.1")
    budget = TransferBudget(15)

    try:
        with pytest.raises(GitNetworkLimitExceeded, match="15 字节"):
            with pinned_http_proxy(target, budget=budget) as proxy_url:
                proxy = urlsplit(proxy_url)
                with socket.create_connection((proxy.hostname, proxy.port), timeout=5) as client:
                    request = f"CONNECT example.com:{origin_port} HTTP/1.1\r\n\r\n"
                    client.sendall(request.encode())
                    assert client.recv(4096).startswith(b"HTTP/1.1 200")
                    client.sendall(b"12345678")
                    assert client.recv(4096) == b""
    finally:
        origin.shutdown()
        origin.server_close()
        origin_thread.join(timeout=5)


def test_plain_http_request_bytes_are_included_in_limit() -> None:
    origin = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _SinkHandler)
    origin_thread = threading.Thread(target=origin.serve_forever, daemon=True)
    origin_thread.start()
    origin_port = int(origin.server_address[1])
    target = PinnedProxyTarget(host="example.com", port=origin_port, address="127.0.0.1")

    try:
        with pytest.raises(GitNetworkLimitExceeded, match="32 字节"):
            with pinned_http_proxy(target, budget=TransferBudget(32)) as proxy_url:
                proxy = urlsplit(proxy_url)
                with socket.create_connection((proxy.hostname, proxy.port), timeout=5) as client:
                    request = f"GET http://example.com:{origin_port}/repo HTTP/1.1\r\nHost: example.com\r\n\r\n"
                    client.sendall(request.encode())
                    assert client.recv(4096) == b""
    finally:
        origin.shutdown()
        origin.server_close()
        origin_thread.join(timeout=5)


def test_proxy_rejects_unvalidated_host() -> None:
    target = PinnedProxyTarget(host="example.com", port=443, address="93.184.216.34")

    with pytest.raises(PermissionError, match="not pinned"):
        _assert_allowed_target("CONNECT", "metadata.internal:443", target)


def test_origin_request_rewrites_host_and_strips_proxy_credentials() -> None:
    target = PinnedProxyTarget(host="example.com", port=8443, address="93.184.216.34")
    header = (
        b"GET https://example.com:8443/path?q=1 HTTP/1.1\r\n"
        b"Host: attacker.example\r\n"
        b"Proxy-Authorization: Basic secret\r\n"
        b"Connection: X-Internal\r\n"
        b"X-Internal: do-not-forward\r\n"
        b"X-Trace: keep\r\n\r\n"
    )

    forwarded = _origin_request(header, "https://example.com:8443/path?q=1", target)

    assert forwarded.startswith(b"GET /path?q=1 HTTP/1.1\r\nHost: example.com:8443\r\n")
    assert b"Proxy-Authorization" not in forwarded
    assert b"X-Internal" not in forwarded
    assert b"X-Trace: keep" in forwarded


def test_restricted_proxy_rejects_private_resolution(monkeypatch) -> None:
    monkeypatch.setattr(
        proxy_module,
        "resolve_host_addresses",
        lambda _host: (_ for _ in ()).throw(ValueError("private address")),
    )

    with restricted_http_proxy(budget=TransferBudget(1024), duration_budget=DurationBudget(60)) as proxy_url:
        proxy = urlsplit(proxy_url)
        with socket.create_connection((proxy.hostname, proxy.port), timeout=5) as client:
            client.sendall(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com\r\n\r\n")
            assert client.recv(4096) == b""


def test_restricted_proxy_ignores_git_private_node_override(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ALLOW_PRIVATE_NODES", True)

    with pytest.raises(ValueError, match="私网|回环|保留"):
        proxy_module.resolve_host_addresses("127.0.0.1")


def test_proxy_rejects_connections_above_task_limit() -> None:
    entered = threading.Event()
    release = threading.Event()

    def blocking_resolver(_method: str, _raw_target: str) -> PinnedProxyTarget:
        entered.set()
        assert release.wait(timeout=_THREAD_JOIN_SECONDS)
        raise PermissionError("test release")

    config = proxy_module._ProxyServerConfig(None, 1, None)
    with proxy_module._run_proxy(blocking_resolver, config) as proxy_url:
        proxy = urlsplit(proxy_url)
        first = socket.create_connection((proxy.hostname, proxy.port), timeout=_THREAD_JOIN_SECONDS)
        try:
            first.sendall(b"CONNECT example.com:443 HTTP/1.1\r\n\r\n")
            assert entered.wait(timeout=_THREAD_JOIN_SECONDS)
            with socket.create_connection((proxy.hostname, proxy.port), timeout=_THREAD_JOIN_SECONDS) as second:
                second.sendall(b"CONNECT example.com:443 HTTP/1.1\r\n\r\n")
                assert second.recv(4096).startswith(_HTTP_SERVICE_UNAVAILABLE)
        finally:
            release.set()
            first.close()


def test_proxy_shutdown_interrupts_active_connection_threads() -> None:
    before = {thread.ident for thread in threading.enumerate()}

    config = proxy_module._ProxyServerConfig(None, 1, None)
    with proxy_module._run_proxy(lambda *_args: (_ for _ in ()).throw(AssertionError()), config) as proxy_url:
        proxy = urlsplit(proxy_url)
        client = socket.create_connection((proxy.hostname, proxy.port), timeout=_THREAD_JOIN_SECONDS)
        try:
            deadline = time.monotonic() + _THREAD_JOIN_SECONDS
            while not any(thread.name == "antcode-pinned-http-connection" for thread in threading.enumerate()):
                if time.monotonic() >= deadline:
                    pytest.fail("proxy request thread did not start")
                time.sleep(0.01)
        finally:
            client.close()

    leaked = [
        thread
        for thread in threading.enumerate()
        if thread.ident not in before and thread.name.startswith("antcode-pinned-http")
    ]
    assert leaked == []


def test_proxy_closes_listener_when_server_thread_cannot_start(monkeypatch) -> None:
    closed = threading.Event()
    original_close = proxy_module._PinnedProxyServer.server_close

    def record_close(server) -> None:
        original_close(server)
        closed.set()

    monkeypatch.setattr(proxy_module._PinnedProxyServer, "server_close", record_close)
    monkeypatch.setattr(threading.Thread, "start", lambda _thread: (_ for _ in ()).throw(RuntimeError("start failed")))

    with pytest.raises(RuntimeError, match="start failed"):
        config = proxy_module._ProxyServerConfig(None, 1, None)
        with proxy_module._run_proxy(lambda *_args: (_ for _ in ()).throw(AssertionError()), config):
            pass

    assert closed.is_set()


def test_restricted_proxy_reports_expired_task_duration(monkeypatch) -> None:
    now = [50.0]
    monkeypatch.setattr(
        "antcode_core.application.services.projects.git_transfer_quota.time.monotonic",
        lambda: now[0],
    )
    duration = DurationBudget(1, label="Rule egress")

    with pytest.raises(NetworkDurationLimitExceeded, match="Rule egress.*1 秒"):
        with restricted_http_proxy(budget=TransferBudget(1024), duration_budget=duration):
            now[0] = 51.0
