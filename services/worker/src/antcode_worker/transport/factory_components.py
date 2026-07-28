"""Protocol checks and runtime client builders used by the transport factory."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from antcode_worker.gateway_endpoint import GatewayAddress
    from antcode_worker.transport.gateway import GatewayConfig

GATEWAY_AUTH_API_KEY = "api_key"
GATEWAY_AUTH_MTLS = "mtls"
CAPABILITY_TIMEOUT_SECONDS = 10.0


def decode_redis_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="strict")
    return str(value)


def resolve_gateway_address(config: Any, *, error_type: type[Exception] = ValueError):
    from antcode_worker.gateway_endpoint import GatewayEndpointError, parse_gateway_address

    try:
        return parse_gateway_address(
            endpoint=config.endpoint,
            host=config.host,
            port=config.port,
        )
    except GatewayEndpointError as exc:
        raise error_type(str(exc)) from exc


def gateway_auth_method(config: Any) -> str:
    if config.client_cert and config.client_key:
        return GATEWAY_AUTH_MTLS
    return GATEWAY_AUTH_API_KEY


def validate_direct_control_url(config: Any) -> None:
    from antcode_worker.app.worker_registration import normalize_api_base_url

    normalize_api_base_url(
        config.api_base_url,
        "",
        allow_insecure_internal=config.allow_insecure_internal,
    )


def validate_gateway_security(config: Any) -> None:
    has_client_cert = bool(config.client_cert)
    has_client_key = bool(config.client_key)
    if has_client_cert != has_client_key:
        raise ValueError("mTLS 必须同时配置 client_cert 和 client_key")
    if has_client_cert and not config.tls:
        raise ValueError("mTLS 客户端证书要求启用 gateway TLS")
    if config.ca_cert and not config.tls:
        raise ValueError("CA 证书要求启用 gateway TLS")


async def check_gateway_capabilities(
    channel: Any,
    *,
    auth_method: str,
    api_key: str | None,
    worker_id: str,
) -> None:
    from antcode_contracts import control_pb2, control_pb2_grpc

    request = control_pb2.CapabilitiesRequest(worker_id=worker_id)
    stub = control_pb2_grpc.ControlServiceStub(channel)
    response = await asyncio.wait_for(
        stub.GetCapabilities(
            request,
            metadata=_gateway_metadata(auth_method, api_key, worker_id),
        ),
        timeout=CAPABILITY_TIMEOUT_SECONDS,
    )
    capabilities = {
        "runtime_control_results_v1": response.runtime_control_results_v1,
        "runtime_control_lease_fencing_v1": response.runtime_control_lease_fencing_v1,
        "runtime_control_deadline_v1": response.runtime_control_deadline_v1,
        "artifact_transfer_v1": response.artifact_transfer_v1,
        "runtime_control_authoritative_clock_v1": response.runtime_control_authoritative_clock_v1,
        "worker_capabilities_v1": response.worker_capabilities_v1,
    }
    missing = next((name for name, supported in capabilities.items() if not supported), None)
    if missing:
        raise RuntimeError(f"Gateway 不支持 {missing}")


def _gateway_metadata(auth_method: str, api_key: str | None, worker_id: str) -> list[tuple[str, str]]:
    metadata: list[tuple[str, str]] = []
    if auth_method == GATEWAY_AUTH_API_KEY and api_key:
        metadata.append(("x-api-key", api_key))
    if worker_id:
        metadata.append(("x-worker-id", worker_id))
    return metadata


def build_gateway_transport_config(
    *,
    spec: Any,
    address: GatewayAddress,
    auth_method: str,
    worker_id: str,
) -> GatewayConfig:
    from antcode_worker.transport.gateway import GatewayConfig

    return GatewayConfig(
        gateway_host=address.host,
        gateway_port=address.port,
        use_tls=spec.tls,
        ca_cert_path=spec.ca_cert,
        client_cert_path=spec.client_cert,
        client_key_path=spec.client_key,
        auth_method=auth_method,
        api_key=spec.api_key,
        worker_id=worker_id,
    )


def build_direct_control_client(config: Any, worker_id: str):
    from antcode_worker.transport.redis.direct_control import DirectControlClient

    return DirectControlClient(
        api_base_url=config.api_base_url,
        worker_id=worker_id,
        api_key=config.api_key,
        secret_key=config.secret_key,
        allow_insecure_internal=config.allow_insecure_internal,
    )


__all__ = [
    "build_direct_control_client",
    "build_gateway_transport_config",
    "check_gateway_capabilities",
    "decode_redis_text",
    "gateway_auth_method",
    "resolve_gateway_address",
    "validate_direct_control_url",
    "validate_gateway_security",
]
