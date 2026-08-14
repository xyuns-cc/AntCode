"""Git remote URL validation and SSRF protection."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from antcode_contracts.network_security import (
    is_metadata_target,
    is_non_public_address,
)
from antcode_contracts.network_security import (
    resolve_host_addresses as resolve_network_host_addresses,
)

from antcode_core.common.config import settings

ALLOWED_GIT_SCHEMES = ("http://", "https://", "ssh://", "git@")
BLOCKED_GIT_HOSTS = frozenset(
    {
        "169.254.169.254",
        "metadata.google.internal",
        "100.100.100.200",
        "fd00:ec2::254",
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "::1",
    }
)
_DEFAULT_PORTS = {"http": 80, "https": 443, "ssh": 22}


@dataclass(frozen=True)
class ResolvedURL:
    """A validated URL bound to the exact addresses used by the connection."""

    url: str
    scheme: str
    host: str
    port: int
    addresses: tuple[str, ...]

    @property
    def primary_address(self) -> str:
        return self.addresses[0]

    def pinned_http_url(self) -> str:
        if self.scheme not in {"http", "https"}:
            raise ValueError(f"URL 不是 HTTP(S): {self.scheme}")
        parsed = urlsplit(self.url)
        address = self.primary_address
        address_literal = f"[{address}]" if ":" in address else address
        default_port = _DEFAULT_PORTS[self.scheme]
        netloc = address_literal if self.port == default_port else f"{address_literal}:{self.port}"
        return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))

    def host_header(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        default_port = _DEFAULT_PORTS[self.scheme]
        return host if self.port == default_port else f"{host}:{self.port}"

    def curl_resolve_value(self) -> str:
        addresses = ",".join(f"[{item}]" if ":" in item else item for item in self.addresses)
        return f"{self.host}:{self.port}:{addresses}"


def _is_private_ip(host: str) -> bool:
    return is_non_public_address(host)


def _is_metadata_target(host: str) -> bool:
    return is_metadata_target(host)


def _extract_host(url: str) -> str:
    if url.lower().startswith("git@"):
        return url[4:].split(":", 1)[0].strip()
    try:
        return (urlsplit(url).hostname or "").strip()
    except ValueError as exc:
        raise ValueError("Git URL 主机格式无效") from exc


def _reject_embedded_credentials(url: str) -> None:
    if url.lower().startswith("git@"):
        return
    parsed = urlsplit(url)
    if parsed.password is not None:
        raise ValueError("Git URL 不允许包含内嵌密码或访问令牌")
    if parsed.scheme.lower() in {"http", "https"} and parsed.username is not None:
        raise ValueError("HTTP(S) Git URL 不允许包含内嵌用户名或访问令牌")


def _resolve_host(host: str, *, require_public: bool) -> tuple[str, ...]:
    return resolve_network_host_addresses(host, allow_private=not require_public)


def resolve_host_addresses(host: str) -> tuple[str, ...]:
    """Resolve a hostname and reject any non-public answer unless explicitly allowed."""
    normalized = (host or "").strip().lower().rstrip(".")
    if not normalized:
        raise ValueError("目标主机不能为空")
    if _is_metadata_target(normalized):
        raise ValueError(f"目标主机禁止指向云元数据端点: {normalized!r}")
    if not settings.ALLOW_PRIVATE_NODES and (normalized in BLOCKED_GIT_HOSTS or _is_private_ip(normalized)):
        raise ValueError(f"目标主机禁止指向本地、私网或云元数据端点: {normalized!r}")
    return _resolve_host(normalized, require_public=not settings.ALLOW_PRIVATE_NODES)


def _resolved_url(url: str, host: str, scheme: str, port: int) -> ResolvedURL:
    addresses = _resolve_host(host, require_public=not settings.ALLOW_PRIVATE_NODES)
    return ResolvedURL(
        url=url,
        scheme=scheme,
        host=host,
        port=port,
        addresses=addresses,
    )


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


def resolve_git_url(url: object) -> ResolvedURL:
    """Validate and resolve a Git URL for connection-time DNS pinning."""
    stripped = _validate_syntax(url)
    host = _extract_host(stripped).lower()
    _reject_embedded_credentials(stripped)
    if not host:
        raise ValueError("Git URL 缺少主机")
    if _is_metadata_target(host):
        raise ValueError(f"Git URL 不合法：禁止指向云元数据端点 {host!r}")
    if not settings.ALLOW_PRIVATE_NODES and (host in BLOCKED_GIT_HOSTS or _is_private_ip(host)):
        raise ValueError(f"Git URL 不合法：禁止指向本地、私网或云元数据端点 {host!r}")
    scheme, port = _git_scheme_and_port(stripped)
    return _resolved_url(stripped, host, scheme, port)


def _git_scheme_and_port(url: str) -> tuple[str, int]:
    if url.lower().startswith("git@"):
        return "ssh", _DEFAULT_PORTS["ssh"]
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    if scheme not in _DEFAULT_PORTS:
        raise ValueError(f"Git URL 协议不支持 DNS pinning: {scheme!r}")
    try:
        port = parsed.port or _DEFAULT_PORTS[scheme]
    except ValueError as exc:
        raise ValueError("Git URL 端口格式无效") from exc
    return scheme, port


def validate_git_url(url: object) -> str:
    """Validate a Git URL and reject local, private, or metadata targets."""
    return resolve_git_url(url).url


ALLOWED_WEBHOOK_SCHEMES = ("http://", "https://")


def resolve_webhook_url(url: object) -> ResolvedURL:
    """Validate and resolve an outbound HTTP URL for connection pinning.

    复用 Git URL 的私网/元数据检查：禁止 localhost、回环、私网、link-local
    及云元数据端点（169.254.169.254 等），仅允许 http/https。
    ``ALLOW_PRIVATE_NODES=true`` 时放行私网地址（内网自建 IM 网关等场景）。
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError("Webhook URL 不能为空")
    stripped = url.strip()
    if not stripped.lower().startswith(ALLOWED_WEBHOOK_SCHEMES):
        raise ValueError("Webhook URL 不合法：仅支持 http:// 或 https://")
    parsed = urlsplit(stripped)
    if parsed.username or parsed.password:
        raise ValueError("Webhook URL 不允许包含用户名或密码")
    host = _extract_host(stripped).lower()
    if not host:
        raise ValueError("Webhook URL 缺少主机")
    if _is_metadata_target(host):
        raise ValueError(f"Webhook URL 不合法：禁止指向云元数据端点 {host!r}")
    if not settings.ALLOW_PRIVATE_NODES and (host in BLOCKED_GIT_HOSTS or _is_private_ip(host)):
        raise ValueError(f"Webhook URL 不合法：禁止指向本地、私网或云元数据端点 {host!r}")
    scheme = parsed.scheme.lower()
    try:
        port = parsed.port or _DEFAULT_PORTS[scheme]
    except ValueError as exc:
        raise ValueError("Webhook URL 端口格式无效") from exc
    return _resolved_url(stripped, host, scheme, port)


def validate_webhook_url(url: object) -> str:
    return resolve_webhook_url(url).url


__all__ = [
    "ResolvedURL",
    "resolve_git_url",
    "resolve_host_addresses",
    "resolve_webhook_url",
    "validate_git_url",
    "validate_webhook_url",
]
