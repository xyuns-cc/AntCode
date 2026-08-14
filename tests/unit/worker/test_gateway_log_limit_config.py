import pytest
from antcode_core.common.log_limits import DEFAULT_LOG_MAX_ENTRY_CONTENT_BYTES
from antcode_worker.transport.gateway import GatewayConfig


def test_gateway_config_rejects_entry_limit_above_shared_contract(monkeypatch) -> None:
    monkeypatch.setenv(
        "GATEWAY_LOG_MAX_ENTRY_CONTENT_BYTES",
        str(DEFAULT_LOG_MAX_ENTRY_CONTENT_BYTES + 1),
    )

    with pytest.raises(ValueError, match="不得超过共享单条日志上限"):
        GatewayConfig()
