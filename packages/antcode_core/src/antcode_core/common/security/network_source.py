"""Network source normalization for security policies."""

from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network, ip_address, ip_network

IPAddress = IPv4Address | IPv6Address
IPNetwork = IPv4Network | IPv6Network


def normalize_ip_or_cidr(value: str | None) -> str | None:
    """Return a canonical IP/CIDR or reject ambiguous hostnames."""
    source = (value or "").strip()
    if not source:
        return None
    try:
        if "/" in source:
            return str(ip_network(source, strict=False))
        return str(ip_address(source))
    except ValueError as exc:
        raise ValueError("来源必须是有效的 IP 地址或 CIDR，不支持主机名") from exc


def parse_trusted_proxy_networks(value: str | None) -> tuple[IPNetwork, ...]:
    """Parse a comma-separated trusted proxy IP/CIDR list."""
    entries = [item.strip() for item in (value or "").split(",") if item.strip()]
    try:
        return tuple(ip_network(item, strict=False) for item in entries)
    except ValueError as exc:
        raise ValueError("ANTCODE_TRUSTED_PROXIES 包含无效 IP 或 CIDR") from exc


def extract_client_ip(
    direct_peer: str,
    forwarded_for: str = "",
    real_ip: str = "",
    *,
    trusted_proxies: str | None = None,
) -> str:
    """Resolve the right-most untrusted hop from a proxy-appended chain."""
    direct = _parse_ip(direct_peer, "socket 对端")
    networks = parse_trusted_proxy_networks(trusted_proxies)
    if not _is_trusted(direct, networks):
        return str(direct)

    if forwarded_for.strip():
        chain = [_parse_ip(item, "X-Forwarded-For") for item in forwarded_for.split(",")]
        return str(_rightmost_untrusted([*chain, direct], networks))
    if real_ip.strip():
        return str(_parse_ip(real_ip, "X-Real-IP"))
    return str(direct)


def _parse_ip(value: str, field_name: str) -> IPAddress:
    candidate = (value or "").strip()
    if not candidate:
        raise ValueError(f"{field_name} 缺少有效 IP 地址")
    try:
        return ip_address(candidate)
    except ValueError as exc:
        raise ValueError(f"{field_name} 包含无效 IP 地址") from exc


def _is_trusted(address: IPAddress, networks: tuple[IPNetwork, ...]) -> bool:
    return any(address.version == network.version and address in network for network in networks)


def _rightmost_untrusted(chain: list[IPAddress], networks: tuple[IPNetwork, ...]) -> IPAddress:
    for address in reversed(chain):
        if not _is_trusted(address, networks):
            return address
    return chain[0]


__all__ = ["extract_client_ip", "normalize_ip_or_cidr", "parse_trusted_proxy_networks"]
