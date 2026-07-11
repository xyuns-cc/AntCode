"""告警管理 API"""

from __future__ import annotations

from antcode_core.application.services.alert import alert_service
from antcode_core.application.services.projects.git_url_security import validate_webhook_url
from antcode_core.common.security.auth import TokenData, get_current_super_admin
from antcode_core.common.serialization import from_json, to_json
from antcode_core.domain.models import SystemConfig, User, UserRole
from antcode_core.domain.schemas.alert import (
    AlertChannelConfig,
    AlertConfigRequest,
    AlertConfigResponse,
    AlertHistoryItem,
    AlertHistoryResponse,
    AlertRateLimitConfig,
    AlertRetryConfig,
    AlertStatsResponse,
    AlertTestRequest,
    AlertTestResponse,
    EmailConfig,
    WebhookConfig,
)
from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger

from antcode_web_api.deps import require_role
from antcode_web_api.response import BaseResponse, success

_REQUIRE_ADMIN = require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN)

router = APIRouter()
_SECRET_MASK = "***REDACTED***"
_JSON_CONFIG_DEFAULTS: dict[str, object] = {
    "feishu_webhooks": [],
    "dingtalk_webhooks": [],
    "wecom_webhooks": [],
    "email_config": {},
}
_BOOLEAN_CONFIG_KEYS = {"rate_limit_enabled", "retry_enabled"}
_INTEGER_CONFIG_KEYS = {"rate_limit_window", "rate_limit_max_count", "max_retries"}


def _mask_webhooks(webhooks: list[dict]) -> list[dict]:
    return [{**item, "url": _SECRET_MASK if item.get("url") else ""} for item in webhooks]


def _merge_webhooks(incoming: list[WebhookConfig], existing: list[dict]) -> list[dict]:
    existing_by_name = {item.get("name"): item for item in existing}
    merged: list[dict] = []
    for webhook in incoming:
        item = webhook.model_dump()
        if item["url"] == _SECRET_MASK:
            old_url = existing_by_name.get(item["name"], {}).get("url")
            if not old_url:
                raise HTTPException(status_code=422, detail=f"Webhook {item['name']} 缺少 URL")
            item["url"] = old_url
        else:
            # SSRF 防护：新增/修改的 Webhook URL 必须是 http(s) 且不指向
            # 本地/私网/云元数据端点（复用 git_url_security 的私网检查）。
            try:
                item["url"] = validate_webhook_url(item["url"])
            except ValueError as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"Webhook {item['name']} URL 不合法: {exc}",
                ) from exc
        merged.append(item)
    return merged


def _masked_email(config: dict) -> EmailConfig:
    masked = dict(config)
    if masked.get("smtp_password"):
        masked["smtp_password"] = _SECRET_MASK
    return EmailConfig(**masked) if masked else EmailConfig()


async def _get_alert_config() -> dict:
    """获取告警配置"""
    config = {
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

    configs = await SystemConfig.filter(category="alert", is_active=True).all()

    for cfg in configs:
        config = _apply_alert_config(config, cfg.config_key, cfg.config_value)

    return config


def _apply_alert_config(config: dict, key: str, value: str) -> dict:
    parsed = _parse_alert_config_value(key, value)
    if parsed is _UNKNOWN_CONFIG:
        return config
    return {**config, key: parsed}


_UNKNOWN_CONFIG = object()


def _parse_alert_config_value(key: str, value: str) -> object:
    if key in _JSON_CONFIG_DEFAULTS:
        return _parse_json_config(key, value)
    if key == "auto_alert_levels":
        return [level.strip() for level in value.split(",") if level.strip()]
    if key in _BOOLEAN_CONFIG_KEYS:
        return value.lower() in ("true", "1", "yes")
    if key in _INTEGER_CONFIG_KEYS:
        return int(value)
    if key == "retry_delay":
        return float(value)
    return _UNKNOWN_CONFIG


def _parse_json_config(key: str, value: str) -> object:
    default = _JSON_CONFIG_DEFAULTS[key]
    try:
        return from_json(value) if value else default
    except Exception:
        return default


async def _save_alert_config(key: str, value: str, value_type: str, description: str, username: str):
    """保存告警配置"""
    existing = await SystemConfig.filter(config_key=key).first()

    if existing:
        existing.config_value = value
        existing.modified_by = username
        await existing.save()
    else:
        await SystemConfig.create(
            config_key=key,
            config_value=value,
            category="alert",
            description=description,
            value_type=value_type,
            is_active=True,
            modified_by=username,
        )


@router.get(
    "/config",
    response_model=BaseResponse[AlertConfigResponse],
    summary="获取告警配置",
    description="获取当前告警配置（管理员）",
)
async def get_alert_config(_admin: User = Depends(_REQUIRE_ADMIN)):
    """获取告警配置"""
    config = await _get_alert_config()

    # 获取告警服务状态
    service_config = alert_service.get_config()

    # 构建邮件配置
    email_config_data = config.get("email_config", {})
    email_config = _masked_email(email_config_data)

    return success(
        AlertConfigResponse(
            channels=AlertChannelConfig(
                feishu_webhooks=[WebhookConfig(**w) for w in _mask_webhooks(config["feishu_webhooks"])],
                dingtalk_webhooks=[WebhookConfig(**w) for w in _mask_webhooks(config["dingtalk_webhooks"])],
                wecom_webhooks=[WebhookConfig(**w) for w in _mask_webhooks(config["wecom_webhooks"])],
                email_config=email_config,
            ),
            auto_alert_levels=config["auto_alert_levels"],
            rate_limit=AlertRateLimitConfig(
                enabled=config["rate_limit_enabled"],
                window=config["rate_limit_window"],
                max_count=config["rate_limit_max_count"],
            ),
            retry=AlertRetryConfig(
                enabled=config["retry_enabled"],
                max_retries=config["max_retries"],
                retry_delay=config["retry_delay"],
            ),
            enabled_channels=service_config.get("enabled_channels", []),
            available_channels=service_config.get("available_channels", []),
        )
    )


@router.put(
    "/config",
    response_model=BaseResponse[dict],
    summary="更新告警配置",
    description="更新告警配置（仅超级管理员）",
)
async def update_alert_config(
    request: AlertConfigRequest,
    current_user: TokenData = Depends(get_current_super_admin),
):
    """更新告警配置"""
    username = current_user.username
    existing_config = await _get_alert_config()
    if request.channels:
        await _save_channel_config(request.channels, existing_config, username)
    if request.auto_alert_levels is not None:
        await _save_alert_config(
            "auto_alert_levels",
            ",".join(request.auto_alert_levels),
            "string",
            "自动告警级别",
            username,
        )
    if request.rate_limit:
        await _save_rate_limit_config(request.rate_limit, username)
    if request.retry:
        await _save_retry_config(request.retry, username)
    await alert_service.reload_config()
    logger.info(f"告警配置已更新 by {username}")
    return success({"updated": True}, message="告警配置已更新")


async def _save_channel_config(channels: AlertChannelConfig, existing: dict, username: str) -> None:
    webhook_configs = (
        ("feishu_webhooks", channels.feishu_webhooks, "飞书 Webhook 配置"),
        ("dingtalk_webhooks", channels.dingtalk_webhooks, "钉钉 Webhook 配置"),
        ("wecom_webhooks", channels.wecom_webhooks, "企业微信 Webhook 配置"),
    )
    for key, incoming, description in webhook_configs:
        if incoming is not None:
            webhooks = _merge_webhooks(incoming, existing[key])
            await _save_alert_config(key, to_json(webhooks), "json", description, username)
    if channels.email_config is not None:
        await _save_email_config(channels.email_config, existing, username)


async def _save_email_config(email: EmailConfig, existing: dict, username: str) -> None:
    email_data = email.model_dump()
    if email_data.get("smtp_password") == _SECRET_MASK:
        email_data["smtp_password"] = existing.get("email_config", {}).get("smtp_password", "")
    await _save_alert_config("email_config", to_json(email_data), "json", "邮件告警配置", username)


async def _save_rate_limit_config(config: AlertRateLimitConfig, username: str) -> None:
    values = (
        ("rate_limit_enabled", str(config.enabled).lower(), "bool", "限流启用"),
        ("rate_limit_window", str(config.window), "int", "限流窗口"),
        ("rate_limit_max_count", str(config.max_count), "int", "限流次数"),
    )
    for key, value, value_type, description in values:
        await _save_alert_config(key, value, value_type, description, username)


async def _save_retry_config(config: AlertRetryConfig, username: str) -> None:
    values = (
        ("retry_enabled", str(config.enabled).lower(), "bool", "重试启用"),
        ("max_retries", str(config.max_retries), "int", "最大重试次数"),
        ("retry_delay", str(config.retry_delay), "float", "重试间隔"),
    )
    for key, value, value_type, description in values:
        await _save_alert_config(key, value, value_type, description, username)


@router.post(
    "/reload",
    response_model=BaseResponse[dict],
    summary="重新加载告警配置",
    description="重新加载告警配置（管理员）",
)
async def reload_alert_config(_admin: User = Depends(_REQUIRE_ADMIN)):
    """重新加载告警配置"""
    await alert_service.reload_config()

    return success({"reloaded": True}, message="告警配置已重新加载")


@router.get(
    "/history",
    response_model=BaseResponse[AlertHistoryResponse],
    summary="获取告警历史",
    description="获取告警历史记录（管理员）",
)
async def get_alert_history(
    limit: int = Query(50, ge=1, le=500, description="返回数量"),
    level: str | None = Query(None, description="按级别过滤"),
    source: str | None = Query(None, description="按来源过滤"),
    _admin: User = Depends(_REQUIRE_ADMIN),
):
    """获取告警历史"""
    history = alert_service.get_history(limit=limit, level=level, source=source)

    return success(AlertHistoryResponse(items=[AlertHistoryItem(**h) for h in history], total=len(history)))


@router.get(
    "/stats",
    response_model=BaseResponse[AlertStatsResponse],
    summary="获取告警统计",
    description="获取告警统计信息（管理员）",
)
async def get_alert_stats(_admin: User = Depends(_REQUIRE_ADMIN)):
    """获取告警统计"""
    stats = alert_service.get_stats()

    return success(AlertStatsResponse(**stats))


@router.post(
    "/test",
    response_model=BaseResponse[AlertTestResponse],
    summary="发送测试告警",
    description="发送测试告警（管理员）",
)
async def send_test_alert(request: AlertTestRequest, _admin: User = Depends(_REQUIRE_ADMIN)):
    """发送测试告警"""
    result = await alert_service.send_test_alert(channel=request.channel)

    return success(AlertTestResponse(**result))
