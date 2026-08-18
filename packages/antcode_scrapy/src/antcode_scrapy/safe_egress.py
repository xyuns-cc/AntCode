"""Force every Scrapy HTTP(S) request through the Worker-owned safe proxy."""

from __future__ import annotations

import logging
import os
from urllib.parse import urlsplit

from antcode_contracts.network_security import (
    HostResolutionUnavailable,
    is_metadata_target,
    is_non_public_address,
    resolve_host_addresses,
)
from scrapy.exceptions import IgnoreRequest

logger = logging.getLogger(__name__)

_REJECT_PREFIX = "Rule 请求 URL 被出网安全策略拒绝"
_EGRESS_POLICY_HINT = (
    "规则爬虫只允许访问公网 HTTP(S) 目标；要抓内网站点需由管理员在能访问该网段的 "
    "Worker 上另行开通，改目标 URL 无法绕过。"
)


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


def _loggable_url(url: str) -> str:
    """只回显调用方自己写的 scheme/host/path，丢掉 userinfo 与 query。"""
    try:
        parsed = urlsplit(url)
    except (TypeError, ValueError):
        return "<无法解析的 URL>"
    if not parsed.hostname:
        return f"{parsed.scheme}:<缺少主机>"
    return f"{parsed.scheme}://{parsed.hostname}{parsed.path}"


def _restricted_target_reason(hostname: str) -> str:
    """给出可区分的拒绝类别，但**绝不回显解析结果**。

    把 hostname 解析到的地址回显给调用方，等于把规则项目变成探测内网的
    地址预言机；类别足以指导整改，地址只对攻击者有价值。
    """
    if is_metadata_target(hostname):
        return "目标主机是云元数据端点"
    if is_non_public_address(hostname):
        return "目标主机是回环 / 私网 / 保留地址"
    return "目标主机名解析到受限地址"


def _reject(url: str, reason: str) -> IgnoreRequest:
    """让拒绝理由离开 Worker 进程。

    Scrapy 对 ``IgnoreRequest`` 刻意不记日志：中间件自己不打，理由就从生成到
    丢弃从未出现在任何地方，用户只看到"退出码: 1"，与"选择器没匹配到"和
    "站点不可达"完全无法区分。这条 error 走 stderr 进 task_logs。
    """
    logger.error("%s: url=%s 原因=%s。%s", _REJECT_PREFIX, _loggable_url(url), reason, _EGRESS_POLICY_HINT)
    return IgnoreRequest(f"{_REJECT_PREFIX}: {reason}")


def _validate_request_url(url: str) -> None:
    try:
        parsed = urlsplit(url)
    except (TypeError, ValueError) as exc:
        raise _reject(url, "URL 格式无效") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise _reject(url, "仅允许 HTTP(S) URL")
    if not parsed.hostname:
        raise _reject(url, "URL 缺少主机")
    if parsed.username or parsed.password:
        raise _reject(url, "URL 不允许包含用户凭证")
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
        # 判定移交是正常路径，沙箱里每个外部请求都会走到，只记 debug 免得刷屏。
        logger.debug("Rule 沙箱内无 DNS，目标主机判定移交 Worker 侧受限代理: url=%s", _loggable_url(url))
        return
    except ValueError as exc:
        raise _reject(url, _restricted_target_reason(parsed.hostname)) from exc


__all__ = ["SafeEgressProxyMiddleware"]
