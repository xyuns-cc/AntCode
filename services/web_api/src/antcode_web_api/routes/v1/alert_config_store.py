"""Persistence and patch semantics for alert configuration."""

import asyncio
from collections.abc import Awaitable, Callable

from antcode_core.application.services.alert.smtp_delivery import validate_smtp_host
from antcode_core.application.services.projects.git_url_security import validate_webhook_url
from antcode_core.common.serialization import from_json, to_json
from antcode_core.domain.models import SystemConfig
from antcode_core.domain.schemas.alert import (
    AlertChannelUpdate,
    AlertConfigRequest,
    AlertRateLimitUpdate,
    AlertRetryUpdate,
    EmailConfig,
    EmailConfigUpdate,
    WebhookConfig,
)
from fastapi import HTTPException

SECRET_MASK = "***REDACTED***"
SaveConfig = Callable[..., Awaitable[object]]
JSON_CONFIG_DEFAULTS: dict[str, object] = {
    "feishu_webhooks": [],
    "dingtalk_webhooks": [],
    "wecom_webhooks": [],
    "email_config": {},
}
BOOLEAN_CONFIG_KEYS = {"rate_limit_enabled", "retry_enabled"}
INTEGER_CONFIG_KEYS = {"rate_limit_window", "rate_limit_max_count", "max_retries"}
UNKNOWN_CONFIG = object()


def mask_webhooks(webhooks: list[dict]) -> list[dict]:
    return [{**item, "url": SECRET_MASK if item.get("url") else ""} for item in webhooks]


def merge_webhooks(incoming: list[WebhookConfig], existing: list[dict]) -> list[dict]:
    """按 WebhookConfig.id 认领已存 URL；name 只是展示字段，用它当键会改名即丢密钥。"""
    _reject_duplicate_ids([webhook.id for webhook in incoming])
    existing_by_id = {item["id"]: item for item in existing if item.get("id")}
    return [_merged_webhook(webhook.model_dump(), existing_by_id) for webhook in incoming]


def _reject_duplicate_ids(ids: list[str]) -> None:
    """重复 id 会让两条 Webhook 认领同一份 URL，下一轮回显后彼此再也分不开。"""
    if len(set(ids)) != len(ids):
        raise HTTPException(status_code=422, detail="Webhook 标识重复")


def _merged_webhook(item: dict, existing_by_id: dict[str, dict]) -> dict:
    if item["url"] != SECRET_MASK:
        return {**item, "url": _validated_webhook_url(item)}
    old_url = existing_by_id.get(item["id"], {}).get("url")
    if not old_url:
        raise HTTPException(status_code=422, detail=f"Webhook {item['name']} 缺少 URL")
    return {**item, "url": old_url}


def _validated_webhook_url(item: dict) -> str:
    try:
        return validate_webhook_url(item["url"])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Webhook {item['name']} URL 不合法: {exc}") from exc


def masked_email(config: dict) -> EmailConfig:
    masked = dict(config)
    if masked.get("smtp_password"):
        masked["smtp_password"] = SECRET_MASK
    return EmailConfig(**masked) if masked else EmailConfig()


async def get_alert_config() -> dict:
    config = _default_alert_config()
    configs = await SystemConfig.filter(category="alert", is_active=True).all()
    for item in configs:
        config = _apply_alert_config(config, item.config_key, item.config_value)
    return config


def _default_alert_config() -> dict:
    return {
        "feishu_webhooks": [],
        "dingtalk_webhooks": [],
        "wecom_webhooks": [],
        "email_config": {},
        "auto_alert_levels": ["ERROR", "CRITICAL"],
        "rate_limit_enabled": True,
        "rate_limit_window": 60,
        "rate_limit_max_count": 3,
        "retry_enabled": True,
        "max_retries": 3,
        "retry_delay": 1.0,
    }


def _apply_alert_config(config: dict, key: str, value: str) -> dict:
    parsed = _parse_alert_config_value(key, value)
    return config if parsed is UNKNOWN_CONFIG else {**config, key: parsed}


def _parse_alert_config_value(key: str, value: str) -> object:
    if key in JSON_CONFIG_DEFAULTS:
        return _parse_json_config(key, value)
    if key == "auto_alert_levels":
        return [level.strip() for level in value.split(",") if level.strip()]
    if key in BOOLEAN_CONFIG_KEYS:
        return value.lower() in ("true", "1", "yes")
    if key in INTEGER_CONFIG_KEYS:
        return int(value)
    return float(value) if key == "retry_delay" else UNKNOWN_CONFIG


def _parse_json_config(key: str, value: str) -> object:
    try:
        return from_json(value) if value else JSON_CONFIG_DEFAULTS[key]
    except Exception:
        return JSON_CONFIG_DEFAULTS[key]


async def save_alert_config(
    key: str,
    *,
    value: str,
    value_type: str,
    description: str,
    username: str,
) -> None:
    existing = await SystemConfig.filter(config_key=key).first()
    if existing:
        existing.config_value = value
        existing.modified_by = username
        await existing.save()
        return
    await SystemConfig.create(
        config_key=key,
        config_value=value,
        category="alert",
        description=description,
        value_type=value_type,
        is_active=True,
        modified_by=username,
    )


async def save_channel_config(
    channels: AlertChannelUpdate,
    existing: dict,
    username: str,
    *,
    save_config: SaveConfig,
) -> None:
    configs = (
        ("feishu_webhooks", channels.feishu_webhooks, "飞书 Webhook 配置"),
        ("dingtalk_webhooks", channels.dingtalk_webhooks, "钉钉 Webhook 配置"),
        ("wecom_webhooks", channels.wecom_webhooks, "企业微信 Webhook 配置"),
    )
    for key, incoming, description in configs:
        if incoming is not None:
            await save_config(
                key,
                value=to_json(merge_webhooks(incoming, existing[key])),
                value_type="json",
                description=description,
                username=username,
            )
    if channels.email_config is not None:
        await save_email_config(channels.email_config, existing, username, save_config=save_config)


async def save_email_config(
    email: EmailConfigUpdate,
    existing: dict,
    username: str,
    *,
    save_config: SaveConfig,
) -> None:
    email_data = {**existing.get("email_config", {}), **email.model_dump(exclude_unset=True)}
    if email_data.get("smtp_password") == SECRET_MASK:
        email_data["smtp_password"] = existing.get("email_config", {}).get("smtp_password", "")
    normalized = EmailConfig.model_validate(email_data).model_dump()
    try:
        normalized["smtp_host"] = await asyncio.to_thread(validate_smtp_host, normalized["smtp_host"])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"SMTP 主机不合法: {exc}") from exc
    await save_config(
        "email_config",
        value=to_json(normalized),
        value_type="json",
        description="邮件告警配置",
        username=username,
    )


async def save_rate_limit_config(
    config: AlertRateLimitUpdate,
    username: str,
    *,
    save_config: SaveConfig,
) -> None:
    values = (
        ("enabled", "rate_limit_enabled", str(config.enabled).lower(), "bool", "限流启用"),
        ("window", "rate_limit_window", str(config.window), "int", "限流窗口"),
        ("max_count", "rate_limit_max_count", str(config.max_count), "int", "限流次数"),
    )
    await _save_submitted_values(config, values, username, save_config=save_config)


async def save_retry_config(
    config: AlertRetryUpdate,
    username: str,
    *,
    save_config: SaveConfig,
) -> None:
    values = (
        ("enabled", "retry_enabled", str(config.enabled).lower(), "bool", "重试启用"),
        ("max_retries", "max_retries", str(config.max_retries), "int", "最大重试次数"),
        ("retry_delay", "retry_delay", str(config.retry_delay), "float", "重试间隔"),
    )
    await _save_submitted_values(config, values, username, save_config=save_config)


async def _save_submitted_values(config, values, username: str, *, save_config: SaveConfig) -> None:
    for field, key, value, value_type, description in values:
        if field in config.model_fields_set:
            await save_config(
                key,
                value=value,
                value_type=value_type,
                description=description,
                username=username,
            )


def updated_alert_fields(request: AlertConfigRequest) -> list[str]:
    fields: list[str] = []
    for field in request.model_fields_set:
        nested = getattr(getattr(request, field), "model_fields_set", None)
        fields.extend([field] if nested is None else [f"{field}.{child}" for child in nested])
    return sorted(fields)
