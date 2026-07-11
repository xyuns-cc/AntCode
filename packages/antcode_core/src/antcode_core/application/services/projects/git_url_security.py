"""Git remote URL validation and SSRF protection."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from antcode_core.common.config import settings

ALLOWED_GIT_SCHEMES = ("http://", "https://", "ssh://", "git@")
BLOCKED_GIT_HOSTS = frozenset(
    {
        "169.254.169.254",
        "metadata.google.internal",
        "100.100.100.200",
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "::1",
    }
)


def _is_private_ip(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private or address.is_loopback or address.is_link_local or address.is_reserved


def _extract_host(url: str) -> str:
    if url.lower().startswith("git@"):
        return url[4:].split(":", 1)[0].strip()
    try:
        return (urlsplit(url).hostname or "").strip()
    except ValueError as exc:
        raise ValueError("Git URL 主机格式无效") from exc


def _validate_resolved_host(host: str) -> None:
    try:
        addresses = {str(item[4][0]) for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise ValueError(f"Git 主机无法解析: {host!r}") from exc
    if not addresses:
        raise ValueError(f"Git 主机没有可用地址: {host!r}")
    blocked = sorted(address for address in addresses if _is_private_ip(address))
    if blocked:
        resolved = ", ".join(blocked)
        raise ValueError(f"Git URL 解析到私网/回环/保留地址: {host!r} -> {resolved}")


def _validate_syntax(url: object) -> str:
    if not isinstance(url, str) or not url.strip():
        raise ValueError("Git URL 不能为空")
    stripped = url.strip()
    if stripped.startswith("-"):
        raise ValueError("Git URL 不合法：不允许以 '-' 开头")
    if "::" in stripped:
        raise ValueError("Git URL 不合法：不允许包含 '::'（git remote helper 语法）")
    if not stripped.lower().startswith(ALLOWED_GIT_SCHEMES):
        raise ValueError(f"Git URL 不合法：仅支持 {', '.join(ALLOWED_GIT_SCHEMES)}")
    return stripped


def validate_git_url(url: object) -> str:
    """Validate a Git URL and reject local, private, or metadata targets."""
    stripped = _validate_syntax(url)
    if settings.ALLOW_PRIVATE_NODES:
        return stripped

    host = _extract_host(stripped).lower()
    if not host:
        raise ValueError("Git URL 缺少主机")
    if host in BLOCKED_GIT_HOSTS or _is_private_ip(host):
        raise ValueError(f"Git URL 不合法：禁止指向本地、私网或云元数据端点 {host!r}")
    _validate_resolved_host(host)
    return stripped


__all__ = ["validate_git_url"]
