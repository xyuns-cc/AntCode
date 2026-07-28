import pytest
from antcode_gateway.config import GatewayConfig


def test_gateway_config_requires_redis_url(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)

    with pytest.raises(ValueError, match="REDIS_URL"):
        GatewayConfig()


def test_gateway_log_limits_are_loaded_as_positive_bytes(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("GATEWAY_LOG_MAX_BATCH_BYTES", "2048")
    monkeypatch.setenv("GATEWAY_LOG_MAX_ENTRY_CONTENT_BYTES", "512")

    config = GatewayConfig()

    assert config.log_max_batch_bytes == 2048
    assert config.log_max_entry_content_bytes == 512


def test_gateway_log_limits_reject_non_positive_values(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("GATEWAY_LOG_MAX_BATCH_BYTES", "0")

    with pytest.raises(ValueError, match="GATEWAY_LOG_MAX_BATCH_BYTES"):
        GatewayConfig()
