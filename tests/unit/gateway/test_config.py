import pytest
from antcode_gateway.config import GatewayConfig


def test_gateway_config_requires_redis_url(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)

    with pytest.raises(ValueError, match="REDIS_URL"):
        GatewayConfig()
