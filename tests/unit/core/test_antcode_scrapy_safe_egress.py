import socket
from types import SimpleNamespace

import pytest
from antcode_scrapy.safe_egress import SafeEgressProxyMiddleware
from antcode_scrapy.settings import build_settings
from scrapy.exceptions import IgnoreRequest

# 单测必须 hermetic：真实 DNS 在部分网络（如透明代理/DNS 劫持环境）会把
# example.com 解析到保留地址 198.18.0.0/15，导致门禁随环境漂移。
_PUBLIC_TEST_IP = "93.184.216.34"


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

    with pytest.raises(IgnoreRequest, match="HTTP"):
        middleware.process_request(request, None)


def test_safe_egress_rejects_url_credentials() -> None:
    middleware = SafeEgressProxyMiddleware("http://127.0.0.1:32001")
    request = SimpleNamespace(url="https://user:secret@example.com/path", meta={})

    with pytest.raises(IgnoreRequest, match="凭证"):
        middleware.process_request(request, None)


def test_proxy_pool_is_rejected_until_it_uses_trusted_parent(monkeypatch) -> None:
    monkeypatch.setenv("ANTCODE_SPIDER_EGRESS_PROXY", "http://127.0.0.1:32001")
    monkeypatch.setenv("ANTCODE_SPIDER_SINK_MODE", "spool")

    with pytest.raises(RuntimeError, match="受控出口"):
        build_settings({"proxy_config": {"enabled": True}})
