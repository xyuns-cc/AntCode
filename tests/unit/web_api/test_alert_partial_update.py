from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.common.serialization import from_json
from antcode_core.domain.schemas.alert import AlertConfigRequest, AlertTestRequest, EmailConfigUpdate
from antcode_web_api.routes.v1 import alert
from fastapi import HTTPException

FEISHU_ID = "a1b2c3d4e5f6708192a3b4c5d6e7f809"


def _stored_config() -> dict:
    return {
        "feishu_webhooks": [{"id": FEISHU_ID, "name": "main", "url": "https://example.com/hook"}],
        "dingtalk_webhooks": [{"name": "ding", "url": "https://example.com/ding"}],
        "wecom_webhooks": [],
        "email_config": {
            "smtp_host": "smtp.example.com",
            "smtp_port": 465,
            "smtp_user": "alerts",
            "smtp_password": "secret",
            "smtp_ssl": True,
            "sender_name": "old",
            "recipients": [],
        },
    }


HTTP_UNPROCESSABLE_ENTITY = 422


@pytest.mark.asyncio
async def test_alert_config_only_persists_submitted_rate_limit_field(monkeypatch) -> None:
    save = AsyncMock()
    audit_write = AsyncMock()
    monkeypatch.setattr(alert, "_get_alert_config", AsyncMock(return_value=_stored_config()))
    monkeypatch.setattr(alert, "_save_alert_config", save)
    monkeypatch.setattr(alert.alert_service, "reload_config", AsyncMock())
    monkeypatch.setattr(alert.audit_service, "log", audit_write)

    response = await alert.update_alert_config(
        AlertConfigRequest(rate_limit={"window": 120}),
        SimpleNamespace(username="root", user_id=1),
    )

    assert response.data == {"updated": True}
    assert save.await_count == 1
    assert save.await_args.args == ("rate_limit_window",)
    assert save.await_args.kwargs["value"] == "120"
    assert audit_write.await_args.kwargs["new_value"] == {"updated_fields": ["rate_limit.window"]}


@pytest.mark.asyncio
async def test_channel_partial_update_does_not_clear_omitted_channels(monkeypatch) -> None:
    save = AsyncMock()
    monkeypatch.setattr(alert, "_save_alert_config", save)
    request = AlertConfigRequest(
        channels={
            "feishu_webhooks": [
                {
                    "id": FEISHU_ID,
                    "name": "main",
                    "url": alert._SECRET_MASK,
                    "levels": ["ERROR"],
                    "enabled": True,
                }
            ]
        }
    )

    await alert._save_channel_config(request.channels, _stored_config(), "root")

    assert save.await_count == 1
    assert save.await_args.args[0] == "feishu_webhooks"
    stored = from_json(save.await_args.kwargs["value"])
    assert stored == [
        {
            "id": FEISHU_ID,
            "name": "main",
            "url": "https://example.com/hook",
            "levels": ["ERROR"],
            "enabled": True,
        }
    ]


@pytest.mark.asyncio
async def test_email_partial_update_preserves_secret_and_omitted_fields(monkeypatch) -> None:
    save = AsyncMock()
    monkeypatch.setattr(alert, "_save_alert_config", save)
    monkeypatch.setattr(alert.alert_config_store, "validate_smtp_host", lambda host: host)

    await alert._save_email_config(EmailConfigUpdate(sender_name="new"), _stored_config(), "root")

    stored = from_json(save.await_args.kwargs["value"])
    assert stored["sender_name"] == "new"
    assert stored["smtp_host"] == "smtp.example.com"
    assert stored["smtp_password"] == "secret"


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["127.0.0.1", "169.254.169.254", "metadata.google.internal"])
async def test_email_config_rejects_private_and_metadata_smtp_hosts(monkeypatch, host: str) -> None:
    save = AsyncMock()
    monkeypatch.setattr(alert, "_save_alert_config", save)

    with pytest.raises(HTTPException) as exc_info:
        await alert._save_email_config(EmailConfigUpdate(smtp_host=host), _stored_config(), "root")

    assert exc_info.value.status_code == HTTP_UNPROCESSABLE_ENTITY
    save.assert_not_awaited()


@pytest.mark.asyncio
async def test_alert_test_route_forwards_requested_channel_and_message(monkeypatch) -> None:
    send = AsyncMock(return_value={"success": True, "message": "ok", "result": {}})
    monkeypatch.setattr(alert.alert_service, "send_test_alert", send)

    await alert.send_test_alert(
        AlertTestRequest(channel="feishu", message="deployment check"),
        SimpleNamespace(),
    )

    send.assert_awaited_once_with(channel="feishu", message="deployment check")
