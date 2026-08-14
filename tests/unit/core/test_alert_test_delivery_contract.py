from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.alert import alert_service as alert_service_singleton
from antcode_core.application.services.alert.alert_manager import alert_manager
from antcode_core.application.services.alert.alert_service import AlertService


@pytest.mark.asyncio
async def test_test_alert_targets_only_requested_channel_and_uses_custom_message(monkeypatch) -> None:
    service = AlertService()
    service._initialized = True
    send = AsyncMock(return_value=("feishu", True, None))
    monkeypatch.setattr(alert_manager, "get_enabled_channels", lambda: ["feishu", "email"])
    monkeypatch.setattr(service, "_send_test_to_channel", send)

    result = await service.send_test_alert("feishu", message="deployment check")

    assert result["success"] is True
    assert result["result"]["enabled_channels"] == ["feishu"]
    send.assert_awaited_once_with("feishu", "deployment check")


@pytest.mark.asyncio
async def test_test_alert_rejects_disabled_requested_channel(monkeypatch) -> None:
    service = AlertService()
    service._initialized = True
    send = AsyncMock()
    monkeypatch.setattr(alert_manager, "get_enabled_channels", lambda: ["email"])
    monkeypatch.setattr(service, "_send_test_to_channel", send)

    result = await service.send_test_alert("feishu", message="deployment check")

    assert result["success"] is False
    assert result["result"]["enabled_channels"] == ["email"]
    send.assert_not_awaited()


def test_alert_service_singleton_uses_same_contract() -> None:
    assert isinstance(alert_service_singleton, AlertService)
