"""Rule 固定 HTTP(S) 代理配置与 Scrapy middleware。"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote, unquote, urlsplit, urlunsplit

SUPPORTED_PROXY_SCHEMES = frozenset({"http", "https"})


class AntCodeProxyMiddleware:
    """把已验证的固定代理写入每个 Scrapy request。"""

    def __init__(self, proxy_url: str) -> None:
        self._proxy_url = proxy_url

    @classmethod
    def from_crawler(cls, crawler):
        rule = getattr(crawler.spider, "rule", {}) if crawler.spider else {}
        proxy_config = (rule or {}).get("proxy_config") or {}
        if not proxy_config.get("enabled"):
            from scrapy.exceptions import NotConfigured

            raise NotConfigured("rule.proxy_config.enabled=False")
        return cls(resolve_fixed_proxy_url(proxy_config))

    def process_request(self, request, spider):
        del spider
        request.meta["proxy"] = self._proxy_url
        return None


def resolve_fixed_proxy_url(proxy_config: dict[str, Any]) -> str:
    """验证固定代理配置；动态代理池与 SOCKS 明确拒绝。"""
    if proxy_config.get("rotation") or proxy_config.get("proxy_list"):
        raise RuntimeError("动态代理池尚未迁移到 Worker 父进程，拒绝在 Rule 子进程中启用")
    raw_url = str(proxy_config.get("proxy_url") or "").strip()
    if not raw_url:
        raise RuntimeError("rule.proxy_config.enabled=True 时 proxy_url 不能为空")
    configured_scheme = str(proxy_config.get("proxy_type") or "").strip().lower()
    candidate = raw_url if "://" in raw_url else f"{configured_scheme or 'http'}://{raw_url}"
    parsed = urlsplit(candidate)
    scheme = parsed.scheme.lower()
    if scheme not in SUPPORTED_PROXY_SCHEMES:
        raise RuntimeError("Rule 固定代理仅支持 http/https；SOCKS 需要父进程受控代理支持")
    if configured_scheme and configured_scheme != scheme:
        raise RuntimeError("proxy_type 与 proxy_url scheme 不一致")
    if not parsed.hostname:
        raise RuntimeError("proxy_url 缺少有效主机")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise RuntimeError("proxy_url 不允许包含 path、query 或 fragment")
    return _build_proxy_url(parsed, proxy_config)


def _build_proxy_url(parsed, proxy_config: dict[str, Any]) -> str:
    configured_user = str(proxy_config.get("username") or "")
    configured_password = str(proxy_config.get("password") or "")
    if (parsed.username or parsed.password) and (configured_user or configured_password):
        raise RuntimeError("代理凭据不能同时写在 proxy_url 和 username/password 字段")
    username = configured_user or unquote(parsed.username or "")
    password = configured_password or unquote(parsed.password or "")
    if password and not username:
        raise RuntimeError("代理 password 已配置但 username 为空")
    credentials = _proxy_credentials(username, password)
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    try:
        port = f":{parsed.port}" if parsed.port is not None else ""
    except ValueError as exc:
        raise RuntimeError("proxy_url 端口无效") from exc
    return urlunsplit((parsed.scheme.lower(), f"{credentials}{host}{port}", "", "", ""))


def _proxy_credentials(username: str, password: str) -> str:
    if not username:
        return ""
    encoded_user = quote(username, safe="")
    encoded_password = quote(password, safe="")
    suffix = f":{encoded_password}" if password else ""
    return f"{encoded_user}{suffix}@"


__all__ = ["AntCodeProxyMiddleware", "resolve_fixed_proxy_url"]
