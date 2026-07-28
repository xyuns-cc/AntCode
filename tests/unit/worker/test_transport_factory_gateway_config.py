"""Gateway factory endpoint and authentication configuration tests."""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from antcode_worker.transport.factory import (
    DirectConfig,
    GatewayConfigSpec,
    TransportConfig,
    TransportConfigError,
    build_gateway_transport_config,
    build_transport_config_from_env,
    create_transport,
    print_transport_banner,
    resolve_gateway_address,
    validate_transport_config,
)


def _gateway_config(**overrides: Any) -> TransportConfig:
    gateway_values = {
        "host": "configured.example.com",
        "port": 50051,
        "api_key": "worker-key",
        **overrides,
    }
    return TransportConfig(
        mode="gateway",
        worker_id="worker-001",
        direct=DirectConfig(),
        gateway=GatewayConfigSpec(**gateway_values),
    )


@pytest.mark.asyncio
async def test_endpoint_overrides_host_and_port_in_gateway_transport():
    config = _gateway_config(endpoint="actual.example.com:5443")

    transport = await create_transport(config, skip_preflight=True)

    assert transport.gateway_config.gateway_host == "actual.example.com"
    assert transport.gateway_config.gateway_port == 5443


def test_endpoint_normalizes_bracketed_ipv6():
    address = resolve_gateway_address(GatewayConfigSpec(endpoint="[2001:db8::1]:5443"))

    assert address.host == "2001:db8::1"
    assert address.port == 5443
    assert address.target == "[2001:db8::1]:5443"


@pytest.mark.parametrize(
    "endpoint",
    (
        "https://gateway.example.com:5443",
        "gateway.example.com:not-a-port",
        "gateway.example.com:70000",
        "gateway.example.com:5443/path",
        "gateway .example.com:5443",
        "   ",
    ),
)
def test_invalid_endpoint_is_rejected(endpoint: str):
    config = _gateway_config(endpoint=endpoint)

    with pytest.raises(TransportConfigError):
        validate_transport_config(config)


@pytest.mark.parametrize(
    ("client_cert", "client_key"),
    (("client.crt", None), (None, "client.key")),
)
def test_mtls_requires_certificate_pair(client_cert: str | None, client_key: str | None):
    config = _gateway_config(
        tls=True,
        client_cert=client_cert,
        client_key=client_key,
    )

    with pytest.raises(TransportConfigError, match="同时配置"):
        validate_transport_config(config)


def test_host_field_must_not_embed_port():
    config = _gateway_config(host="gateway.example.com:5443")

    with pytest.raises(TransportConfigError, match="不得包含端口"):
        validate_transport_config(config)


def test_mtls_requires_tls():
    config = _gateway_config(
        tls=False,
        client_cert="client.crt",
        client_key="client.key",
    )

    with pytest.raises(TransportConfigError, match="启用 gateway TLS"):
        validate_transport_config(config)


def test_ca_certificate_requires_tls():
    config = _gateway_config(tls=False, ca_cert="ca.crt")

    with pytest.raises(TransportConfigError, match="CA 证书"):
        validate_transport_config(config)


def test_mtls_runtime_config_and_banner_match():
    config = _gateway_config(
        endpoint="mtls.example.com:5443",
        tls=True,
        client_cert="client.crt",
        client_key="client.key",
    )
    log = MagicMock()

    validate_transport_config(config)
    runtime_config = build_gateway_transport_config(config)
    with patch("antcode_worker.transport.factory.logger.info", log):
        print_transport_banner(config)

    banner = "\n".join(str(call.args[0]) for call in log.call_args_list)
    assert runtime_config.auth_method == "mtls"
    assert runtime_config.use_tls is True
    assert "Endpoint: mtls.example.com:5443" in banner
    assert "TLS:      ON" in banner
    assert "Auth:     mTLS" in banner


def test_api_key_runtime_config_uses_api_key_authentication():
    runtime_config = build_gateway_transport_config(_gateway_config())

    assert runtime_config.auth_method == "api_key"
    assert runtime_config.api_key == "worker-key"


def test_factory_env_endpoint_is_preserved_for_runtime_resolution():
    with patch.dict(
        "os.environ",
        {
            "WORKER_GATEWAY_ENDPOINT": "endpoint.example.com:5443",
            "WORKER_GATEWAY_HOST": "ignored.example.com",
            "WORKER_GATEWAY_PORT": "50052",
        },
        clear=True,
    ):
        config = build_transport_config_from_env(worker_id="worker-001")

    runtime_config = build_gateway_transport_config(config)
    assert runtime_config.gateway_host == "endpoint.example.com"
    assert runtime_config.gateway_port == 5443


def test_explicit_false_tls_overrides_environment_true():
    with patch.dict("os.environ", {"WORKER_GATEWAY_TLS": "true"}, clear=True):
        config = build_transport_config_from_env(
            worker_id="worker-001",
            gateway_tls=False,
        )

    assert config.gateway.tls is False


def test_worker_config_loads_gateway_tls_environment():
    from antcode_worker.config import _load_gateway_env

    with patch.dict(
        "os.environ",
        {
            "WORKER_GATEWAY_ENDPOINT": "mtls.example.com:5443",
            "WORKER_GATEWAY_TLS": "true",
            "WORKER_CA_CERT": "ca.crt",
            "WORKER_CLIENT_CERT": "client.crt",
            "WORKER_CLIENT_KEY": "client.key",
        },
        clear=True,
    ):
        values = _load_gateway_env()

    assert values == {
        "gateway_host": "mtls.example.com",
        "gateway_port": 5443,
        "gateway_tls": True,
        "ca_cert": "ca.crt",
        "client_cert": "client.crt",
        "client_key": "client.key",
    }


def test_worker_config_rejects_invalid_gateway_port():
    from antcode_worker.config import _load_gateway_env

    with patch.dict("os.environ", {"WORKER_GATEWAY_PORT": "invalid"}, clear=True):
        with pytest.raises(ValueError, match="WORKER_GATEWAY_PORT"):
            _load_gateway_env()
