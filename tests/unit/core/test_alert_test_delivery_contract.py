from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.alert import alert_service as alert_service_singleton
from antcode_core.application.services.alert.alert_manager import alert_manager
from antcode_core.application.services.alert.alert_service import AlertService


def _service_with_stubbed_db() -> AlertService:
    """send_test_alert 现在以 DB 为准重建渠道（多进程下不能信进程内旧状态）。"""
    service = AlertService()
    service._load_config_from_db = AsyncMock(return_value=AlertService._default_config())
    service._apply_config = AsyncMock()
    return service


@pytest.mark.asyncio
async def test_test_alert_targets_only_requested_channel_and_uses_custom_message(monkeypatch) -> None:
    service = _service_with_stubbed_db()
    send = AsyncMock(return_value=("feishu", True, None))
    monkeypatch.setattr(alert_manager, "get_enabled_channels", lambda: ["feishu", "email"])
    monkeypatch.setattr(service, "_send_test_to_channel", send)

    result = await service.send_test_alert("feishu", message="deployment check")

    assert result["success"] is True
    assert result["result"]["enabled_channels"] == ["feishu"]
    send.assert_awaited_once_with("feishu", "deployment check")


@pytest.mark.asyncio
async def test_test_alert_rejects_disabled_requested_channel(monkeypatch) -> None:
    service = _service_with_stubbed_db()
    send = AsyncMock()
    monkeypatch.setattr(alert_manager, "get_enabled_channels", lambda: ["email"])
    monkeypatch.setattr(service, "_send_test_to_channel", send)

    result = await service.send_test_alert("feishu", message="deployment check")

    assert result["success"] is False
    assert result["result"]["enabled_channels"] == ["email"]
    send.assert_not_awaited()


def test_alert_service_singleton_uses_same_contract() -> None:
    assert isinstance(alert_service_singleton, AlertService)
