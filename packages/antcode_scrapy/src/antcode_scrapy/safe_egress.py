"""Force every Scrapy HTTP(S) request through the Worker-owned safe proxy."""

from __future__ import annotations

import os
from urllib.parse import urlsplit

from antcode_contracts.network_security import (
    HostResolutionUnavailable,
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
    # 同款纯解析器，但 Rule 子进程始终传 allow_private=False，不继承应用层
    # ALLOW_PRIVATE_NODES。playwright 引擎下 Chromium 对 loopback
    # 默认绕过代理直连,此校验拦住顶层导航 URL,子资源由 --proxy-bypass-list
    # 强制回代理二次拦截。
    try:
        resolve_host_addresses(parsed.hostname, allow_private=False)
    except HostResolutionUnavailable:
        # Rule 子进程跑在 --unshare-net 命名空间里,只有 loopback、没有 DNS
        # (rule_network_policy.rule_bridge_socket 禁止共享 Worker network ns)。
        # 能在这里解析成功的只有 /etc/hosts 条目与 IP 字面量 —— localhost、
        # 169.254.169.254、metadata.google.internal 全部走上面的分支照旧拒绝,
        # 而它们正是 Chromium 可能绕过代理直连的那一类目标,拦截未被削弱。
        # 剩下"解析不出来"的只能是外部域名: 同一命名空间里任何进程(含 Chromium)
        # 也解析不了、连不上,流量必然经 ANTCODE_SPIDER_EGRESS_PROXY 回到 Worker
        # 侧 pinned proxy,由它用真实 DNS 做权威判定并实际建连(见
        # pinned_http_proxy.restricted_http_proxy.resolve,同样 allow_private=False)。
        # 该 proxy 由 engine.rule_egress.rule_egress_plan 对所有 rule 计划无条件
        # 启用,与 sandbox_mode 无关,因此没有绕过它的出口。此处继续判定只会把全部
        # 外部域名误杀成 IgnoreRequest。
        return
    except ValueError as exc:
        raise IgnoreRequest(f"Rule 请求 URL 指向受限网络地址: {exc}") from exc


__all__ = ["SafeEgressProxyMiddleware"]
