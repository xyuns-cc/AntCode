from antcode_core.domain.schemas.alert import WebhookConfig
from antcode_web_api.routes.v1 import alert


def test_alert_response_masks_webhook_and_email_secrets():
    webhooks = alert._mask_webhooks([{"name": "ops", "url": "https://secret", "enabled": True}])
    email = alert._masked_email({"smtp_password": "password"})

    assert webhooks[0]["url"] == alert._SECRET_MASK
    assert email.smtp_password == alert._SECRET_MASK


def test_masked_webhook_update_preserves_existing_secret():
    incoming = [WebhookConfig(name="ops", url=alert._SECRET_MASK)]

    merged = alert._merge_webhooks(incoming, [{"name": "ops", "url": "https://secret"}])

    assert merged[0]["url"] == "https://secret"
