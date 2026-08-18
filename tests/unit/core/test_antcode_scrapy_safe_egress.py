import os
import socket
import subprocess
import sys
from types import SimpleNamespace

import pytest
from antcode_contracts.network_security import HostResolutionUnavailable, resolve_host_addresses
from antcode_scrapy.safe_egress import SafeEgressProxyMiddleware
from antcode_scrapy.settings import build_settings
from scrapy.exceptions import IgnoreRequest

# 单测必须 hermetic：真实 DNS 在部分网络（如透明代理/DNS 劫持环境）会把
# example.com 解析到保留地址 198.18.0.0/15，导致门禁随环境漂移。
_PUBLIC_TEST_IP = "93.184.216.34"


def _raise_gaierror(*_args, **_kwargs):
    """复刻 Rule 沙箱 ``--unshare-net`` 下的实测行为：[Errno -3] 无法解析。"""
    raise socket.gaierror(-3, "Temporary failure in name resolution")


@pytest.fixture(autouse=True)
def _hermetic_dns(monkeypatch):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (_PUBLIC_TEST_IP, port or 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


def test_scrapy_and_playwright_use_worker_safe_proxy(monkeypatch) -> None:
    proxy_url = "http://127.0.0.1:32001"
    monkeypatch.setenv("ANTCODE_SPIDER_EGRESS_PROXY", proxy_url)
    monkeypatch.setenv("ANTCODE_SPIDER_SINK_MODE", "spool")

    settings = build_settings({"engine": "playwright"})

    assert settings["DOWNLOADER_MIDDLEWARES"] == {
        "antcode_scrapy.safe_egress.SafeEgressProxyMiddleware": 749,
    }
    assert settings["PLAYWRIGHT_LAUNCH_OPTIONS"]["proxy"]["server"] == proxy_url


def test_safe_egress_middleware_overwrites_request_proxy() -> None:
    middleware = SafeEgressProxyMiddleware("http://127.0.0.1:32001")
    request = SimpleNamespace(url="https://example.com/path", meta={"proxy": "http://attacker-proxy:8080"})

    middleware.process_request(request, None)

    assert request.meta["proxy"] == "http://127.0.0.1:32001"


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "data:text/plain,secret", "ftp://example.com/file"],
)
def test_safe_egress_rejects_non_http_urls(url: str) -> None:
    middleware = SafeEgressProxyMiddleware("http://127.0.0.1:32001")
    request = SimpleNamespace(url=url, meta={})

    with pytest.raises(IgnoreRequest, match="仅允许 HTTP"):
        middleware.process_request(request, None)


def test_safe_egress_rejects_url_credentials() -> None:
    middleware = SafeEgressProxyMiddleware("http://127.0.0.1:32001")
    request = SimpleNamespace(url="https://user:secret@example.com/path", meta={})

    with pytest.raises(IgnoreRequest, match="凭证"):
        middleware.process_request(request, None)


def test_safe_egress_rejects_private_dns_answer(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))],
    )
    middleware = SafeEgressProxyMiddleware("http://127.0.0.1:32001")

    with pytest.raises(IgnoreRequest, match="出网安全策略拒绝"):
        middleware.process_request(SimpleNamespace(url="https://example.com/", meta={}), None)


def test_safe_egress_rejects_hosts_file_loopback_without_dns(monkeypatch) -> None:
    """沙箱 netns 无 DNS，但 /etc/hosts 仍能解析 localhost —— 必须照旧拦住。

    这正是 Chromium 绕过代理直连的那一类目标，实测 ``--unshare-net`` 下
    ``localhost -> 127.0.0.1, ::1`` 解析成功。
    """
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))],
    )
    middleware = SafeEgressProxyMiddleware("http://127.0.0.1:32001")

    with pytest.raises(IgnoreRequest, match="出网安全策略拒绝"):
        middleware.process_request(SimpleNamespace(url="http://localhost/", meta={}), None)


def test_safe_egress_rejects_metadata_host_even_without_dns(monkeypatch) -> None:
    """云元数据主机按名字判定，不依赖 DNS，无解析器时也必须拒绝。"""
    monkeypatch.setattr(socket, "getaddrinfo", _raise_gaierror)
    middleware = SafeEgressProxyMiddleware("http://127.0.0.1:32001")

    with pytest.raises(IgnoreRequest, match="出网安全策略拒绝"):
        middleware.process_request(
            SimpleNamespace(url="http://metadata.google.internal/", meta={}),
            None,
        )


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://10.0.0.5/", "http://169.254.169.254/"])
def test_safe_egress_rejects_ip_literals_even_without_dns(monkeypatch, url: str) -> None:
    """IP 字面量在解析前就判定，无 DNS 环境下拦截不受影响。"""
    monkeypatch.setattr(socket, "getaddrinfo", _raise_gaierror)
    middleware = SafeEgressProxyMiddleware("http://127.0.0.1:32001")

    with pytest.raises(IgnoreRequest, match="出网安全策略拒绝"):
        middleware.process_request(SimpleNamespace(url=url, meta={}), None)


def test_safe_egress_delegates_unresolvable_public_host_to_pinned_proxy(monkeypatch) -> None:
    """沙箱内解析不了的外部域名不再被误杀，交给有 DNS 的 Worker 侧 pinned proxy。"""
    monkeypatch.setattr(socket, "getaddrinfo", _raise_gaierror)
    middleware = SafeEgressProxyMiddleware("http://127.0.0.1:32001")
    request = SimpleNamespace(url="https://example.com/", meta={})

    middleware.process_request(request, None)

    assert request.meta["proxy"] == "http://127.0.0.1:32001"


def test_resolver_distinguishes_missing_dns_from_restricted_answer(monkeypatch) -> None:
    """契约层必须让"无 DNS"与"解析到受限地址"是两个可区分的异常类型。"""
    monkeypatch.setattr(socket, "getaddrinfo", _raise_gaierror)
    with pytest.raises(HostResolutionUnavailable):
        resolve_host_addresses("example.com", allow_private=False)

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))],
    )
    with pytest.raises(ValueError) as restricted:
        resolve_host_addresses("example.com", allow_private=False)
    assert not isinstance(restricted.value, HostResolutionUnavailable)


def test_safe_egress_import_does_not_require_application_settings(tmp_path) -> None:
    env = os.environ.copy()
    for name in ("DATABASE_URL", "JWT_SECRET", "ENCRYPTION_KEY", "ENCRYPTION_KEY_SALT"):
        env.pop(name, None)
    code = """
import builtins

original_import = builtins.__import__

def reject_core_import(name, *args, **kwargs):
    if name == "antcode_core" or name.startswith("antcode_core."):
        raise AssertionError(f"unexpected application dependency: {name}")
    return original_import(name, *args, **kwargs)

builtins.__import__ = reject_core_import
import antcode_scrapy.safe_egress
"""

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr


def test_proxy_pool_is_rejected_until_it_uses_trusted_parent(monkeypatch) -> None:
    monkeypatch.setenv("ANTCODE_SPIDER_EGRESS_PROXY", "http://127.0.0.1:32001")
    monkeypatch.setenv("ANTCODE_SPIDER_SINK_MODE", "spool")

    with pytest.raises(RuntimeError, match="受控出口"):
        build_settings({"proxy_config": {"enabled": True}})
