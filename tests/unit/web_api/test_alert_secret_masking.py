import pytest
from antcode_core.domain.schemas.alert import WebhookConfig
from antcode_web_api.routes.v1 import alert
from fastapi import HTTPException

HTTP_UNPROCESSABLE_ENTITY = 422
FIRST_ID = "1f0c1a3b4d5e6f708192a3b4c5d6e7f8"
SECOND_ID = "90a1b2c3d4e5f60718293a4b5c6d7e8f"
FIRST_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/first"
SECOND_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/second"


def _stored(webhook_id: str, name: str, url: str) -> dict:
    return {"id": webhook_id, "name": name, "url": url, "levels": ["ERROR"], "enabled": True}


def _submitted(webhook_id: str, name: str) -> WebhookConfig:
    """回显给前端的 URL 是掩码，原样提交表示「保持原值」。"""
    return WebhookConfig(id=webhook_id, name=name, url=alert._SECRET_MASK, levels=["ERROR"], enabled=True)


def test_alert_response_masks_webhook_and_email_secrets():
    webhooks = alert._mask_webhooks([{"name": "ops", "url": "https://secret", "enabled": True}])
    email = alert._masked_email({"smtp_password": "password"})

    assert webhooks[0]["url"] == alert._SECRET_MASK
    assert email.smtp_password == alert._SECRET_MASK


def test_masked_webhook_update_preserves_existing_secret():
    merged = alert._merge_webhooks([_submitted(FIRST_ID, "ops")], [_stored(FIRST_ID, "ops", FIRST_URL)])

    assert merged[0]["url"] == FIRST_URL


def test_renamed_webhook_keeps_its_own_secret():
    """改名是本缺陷的触发路径：按 name 认领时找不到旧记录，密钥被判成缺失。"""
    merged = alert._merge_webhooks([_submitted(FIRST_ID, "ops-renamed")], [_stored(FIRST_ID, "ops", FIRST_URL)])

    assert merged[0]["name"] == "ops-renamed"
    assert merged[0]["url"] == FIRST_URL


def test_swapped_names_do_not_swap_secrets():
    """互换两条的展示名不得让告警发到对方的地址上。"""
    existing = [_stored(FIRST_ID, "first", FIRST_URL), _stored(SECOND_ID, "second", SECOND_URL)]

    merged = alert._merge_webhooks([_submitted(FIRST_ID, "second"), _submitted(SECOND_ID, "first")], existing)

    assert [(item["id"], item["url"]) for item in merged] == [(FIRST_ID, FIRST_URL), (SECOND_ID, SECOND_URL)]


def test_duplicate_display_names_keep_separate_secrets():
    """展示名没有唯一约束，同名两条必须各自认领自己的 URL。"""
    existing = [_stored(FIRST_ID, "ops", FIRST_URL), _stored(SECOND_ID, "ops", SECOND_URL)]

    merged = alert._merge_webhooks([_submitted(FIRST_ID, "ops"), _submitted(SECOND_ID, "ops")], existing)

    assert [item["url"] for item in merged] == [FIRST_URL, SECOND_URL]


def test_deleting_and_reordering_keeps_each_secret():
    existing = [_stored(FIRST_ID, "first", FIRST_URL), _stored(SECOND_ID, "second", SECOND_URL)]

    merged = alert._merge_webhooks([_submitted(SECOND_ID, "second")], existing)

    assert [(item["id"], item["url"]) for item in merged] == [(SECOND_ID, SECOND_URL)]


def test_masked_url_without_known_id_is_rejected():
    """新建条目提交掩码时无处认领，只能报错——不得回退到任何一条已存记录。"""
    with pytest.raises(HTTPException) as exc_info:
        alert._merge_webhooks(
            [WebhookConfig(name="ops", url=alert._SECRET_MASK)], [_stored(FIRST_ID, "ops", FIRST_URL)]
        )

    assert exc_info.value.status_code == HTTP_UNPROCESSABLE_ENTITY


def test_duplicate_ids_are_rejected():
    """重复 id 会让两条认领同一份 URL，回显后彼此再也分不开。"""
    with pytest.raises(HTTPException) as exc_info:
        alert._merge_webhooks(
            [_submitted(FIRST_ID, "ops"), _submitted(FIRST_ID, "ops-copy")],
            [_stored(FIRST_ID, "ops", FIRST_URL)],
        )

    assert exc_info.value.status_code == HTTP_UNPROCESSABLE_ENTITY


def test_new_webhook_receives_unique_server_issued_id(monkeypatch):
    """id 由服务端签发；同名同 URL 的两次新建也必须拿到互不相同的身份。"""
    monkeypatch.setattr(alert.alert_config_store, "validate_webhook_url", lambda url: url)
    existing = [_stored(FIRST_ID, "ops", FIRST_URL)]

    merged = alert._merge_webhooks(
        [WebhookConfig(name="ops", url=SECOND_URL), WebhookConfig(name="ops", url=SECOND_URL)],
        existing,
    )

    assert merged[0]["id"] != merged[1]["id"]
    assert FIRST_ID not in {item["id"] for item in merged}
