"""Force every Scrapy HTTP(S) request through the Worker-owned safe proxy."""

from __future__ import annotations

import os
from urllib.parse import urlsplit

from antcode_core.application.services.projects.git_url_security import (
    resolve_host_addresses,
)
from scrapy.exceptions import IgnoreRequest


class SafeEgressProxyMiddleware:
    def __init__(self, proxy_url: str) -> None:
        if not proxy_url.startswith("http://127.0.0.1:"):
            raise ValueError("Rule safe egress proxy 必须绑定 Worker loopback")
        self._proxy_url = proxy_url

    @classmethod
    def from_crawler(cls, crawler):
        del crawler
        proxy_url = os.environ.get("ANTCODE_SPIDER_EGRESS_PROXY", "").strip()
        if not proxy_url:
            raise RuntimeError("Rule 缺少 ANTCODE_SPIDER_EGRESS_PROXY")
        return cls(proxy_url)

    def process_request(self, request, spider):
        del spider
        _validate_request_url(request.url)
        request.meta["proxy"] = self._proxy_url
        return None


def _validate_request_url(url: str) -> None:
    try:
        parsed = urlsplit(url)
    except (TypeError, ValueError) as exc:
        raise IgnoreRequest("Rule 请求 URL 格式无效") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise IgnoreRequest("Rule 仅允许访问 HTTP(S) URL")
    if not parsed.hostname:
        raise IgnoreRequest("Rule 请求 URL 缺少主机")
    if parsed.username or parsed.password:
        raise IgnoreRequest("Rule 请求 URL 不允许包含用户凭证")
    # SSRF fail-closed: 拒绝回环/私网/链路本地/云元数据目标。复用 Git/webhook
    # 同款解析器,与仓库其它出口硬化行为一致（ALLOW_PRIVATE_NODES=true 时按内网
    # 场景放行私网,元数据端点始终拒绝）。playwright 引擎下 Chromium 对 loopback
    # 默认绕过代理直连,此校验拦住顶层导航 URL,子资源由 --proxy-bypass-list
    # 强制回代理二次拦截。
    try:
        resolve_host_addresses(parsed.hostname)
    except ValueError as exc:
        raise IgnoreRequest(f"Rule 请求 URL 指向受限网络地址: {exc}") from exc


__all__ = ["SafeEgressProxyMiddleware"]
