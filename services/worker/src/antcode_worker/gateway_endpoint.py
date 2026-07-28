"""Pure Gateway endpoint parsing shared by config and transport wiring."""

from dataclasses import dataclass
from ipaddress import IPv6Address
from urllib.parse import urlsplit

DEFAULT_GATEWAY_HOST = "localhost"
DEFAULT_GATEWAY_PORT = 50051
MIN_NETWORK_PORT = 1
MAX_NETWORK_PORT = 65535


class GatewayEndpointError(ValueError):
    """Gateway endpoint is not a valid gRPC host and port."""


@dataclass(frozen=True)
class GatewayAddress:
    """Normalized Gateway address."""

    host: str
    port: int
    target: str


def parse_gateway_address(*, endpoint: str, host: str, port: int) -> GatewayAddress:
    """Parse endpoint, which takes precedence over separate host and port."""
    raw_endpoint = endpoint.strip()
    if endpoint and not raw_endpoint:
        raise GatewayEndpointError("gateway.endpoint 不能为空白字符串")
    if not raw_endpoint:
        return _gateway_address(host, port)
    if "://" in raw_endpoint:
        raise GatewayEndpointError("gateway.endpoint 必须使用 host:port 格式，不能包含 URL scheme")
    try:
        parsed = urlsplit(f"//{raw_endpoint}")
        resolved_port = parsed.port if parsed.port is not None else port
    except ValueError as exc:
        raise GatewayEndpointError(f"无效的 gateway.endpoint: {raw_endpoint}") from exc
    if parsed.path or parsed.query or parsed.fragment or parsed.username:
        raise GatewayEndpointError("gateway.endpoint 只能包含 host:port")
    return _gateway_address(parsed.hostname or "", resolved_port)


def _gateway_address(host: str, port: int) -> GatewayAddress:
    normalized_host = host.strip()
    if normalized_host.startswith("[") and normalized_host.endswith("]"):
        normalized_host = normalized_host[1:-1]
    if not normalized_host:
        raise GatewayEndpointError("gateway host 不能为空")
    if any(character.isspace() or character in "/?#@" for character in normalized_host):
        raise GatewayEndpointError("gateway host 包含非法字符")
    if ":" in normalized_host:
        try:
            IPv6Address(normalized_host)
        except ValueError as exc:
            raise GatewayEndpointError("gateway host 不得包含端口；请单独配置 gateway port") from exc
    if isinstance(port, bool) or not isinstance(port, int):
        raise GatewayEndpointError("gateway port 必须是整数")
    if not MIN_NETWORK_PORT <= port <= MAX_NETWORK_PORT:
        raise GatewayEndpointError(f"gateway port 超出有效范围: {port}")
    target_host = f"[{normalized_host}]" if ":" in normalized_host else normalized_host
    return GatewayAddress(host=normalized_host, port=port, target=f"{target_host}:{port}")
