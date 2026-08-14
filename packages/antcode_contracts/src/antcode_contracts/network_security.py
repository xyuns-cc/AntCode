"""Configuration-free host resolution for outbound security checks."""

from __future__ import annotations

import ipaddress
import socket


class HostResolutionUnavailable(ValueError):
    """DNS 不可达导致无法判定，区别于"已解析且指向受限地址"。

    两者语义相反：后者是确定的拒绝理由，前者只说明本进程拿不到答案。
    调用方若处在无 DNS 的网络命名空间且另有权威校验方（见
    ``antcode_scrapy.safe_egress``），可以据此把判定交给那一方；默认按
    ``ValueError`` 捕获的调用方行为不变，仍然 fail-closed。
    """


METADATA_HOSTS = frozenset(
    {
        "169.254.169.254",
        "100.100.100.200",
        "fd00:ec2::254",
        "metadata.google.internal",
    }
)


def is_non_public_address(host: str) -> bool:
    """Return whether *host* is an IP literal that is not globally routable."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return not address.is_global


def is_metadata_target(host: str) -> bool:
    """Recognize cloud metadata hosts, including IPv4-mapped IPv6 literals."""
    normalized = (host or "").strip().lower().rstrip(".")
    if normalized in METADATA_HOSTS:
        return True
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    return mapped is not None and str(mapped) in METADATA_HOSTS


def resolve_host_addresses(host: str, *, allow_private: bool) -> tuple[str, ...]:
    """Resolve *host*, always rejecting metadata and optionally private answers."""
    normalized = (host or "").strip().lower().rstrip(".")
    if not normalized:
        raise ValueError("目标主机不能为空")
    if is_metadata_target(normalized):
        raise ValueError(f"目标主机禁止指向云元数据端点: {normalized!r}")
    if not allow_private and is_non_public_address(normalized):
        raise ValueError(f"目标主机禁止指向本地、私网或保留地址: {normalized!r}")
    try:
        resolved = {str(item[4][0]) for item in socket.getaddrinfo(normalized, None, type=socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise HostResolutionUnavailable(f"目标主机无法解析: {normalized!r}") from exc
    if not resolved:
        raise ValueError(f"目标主机没有可用地址: {normalized!r}")
    metadata = sorted(address for address in resolved if is_metadata_target(address))
    if metadata:
        raise ValueError(f"目标主机解析到云元数据端点: {normalized!r} -> {', '.join(metadata)}")
    blocked = sorted(address for address in resolved if not allow_private and is_non_public_address(address))
    if blocked:
        raise ValueError(f"目标主机解析到私网/回环/保留地址: {normalized!r} -> {', '.join(blocked)}")
    return tuple(sorted(resolved, key=lambda item: (":" in item, item)))
