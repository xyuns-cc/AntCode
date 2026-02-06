"""告警管理 API"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from loguru import logger

from antcode_web_api.response import BaseResponse, success
from antcode_core.common.security.auth import TokenData, get_current_super_admin, get_current_user
from antcode_core.common.serialization import from_json, to_json
from antcode_core.domain.models import SystemConfig, User
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
from antcode_core.application.services.alert import alert_service

router = APIRouter()


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
        key = cfg.config_key
        value = cfg.config_value

        if key == "feishu_webhooks":
            try:
                config["feishu_webhooks"] = from_json(value) if value else []
            except Exception:
                config["feishu_webhooks"] = []
        elif key == "dingtalk_webhooks":
            try:
                config["dingtalk_webhooks"] = from_json(value) if value else []
            except Exception:
                config["dingtalk_webhooks"] = []
        elif key == "wecom_webhooks":
            try:
                config["wecom_webhooks"] = from_json(value) if value else []
            except Exception:
                config["wecom_webhooks"] = []
        elif key == "email_config":
            try:
                config["email_config"] = from_json(value) if value else {}
            except Exception:
                config["email_config"] = {}
        elif key == "auto_alert_levels":
            config["auto_alert_levels"] = [
                level.strip() for level in value.split(",") if level.strip()
            ]
        elif key == "rate_limit_enabled":
            config["rate_limit_enabled"] = value.lower() in ("true", "1", "yes")
        elif key == "rate_limit_window":
            config["rate_limit_window"] = int(value)
        elif key == "rate_limit_max_count":
            config["rate_limit_max_count"] = int(value)
        elif key == "retry_enabled":
            config["retry_enabled"] = value.lower() in ("true", "1", "yes")
        elif key == "max_retries":
            config["max_retries"] = int(value)
        elif key == "retry_delay":
            config["retry_delay"] = float(value)

    return config


async def _save_alert_config(
    key: str, value: str, value_type: str, description: str, username: str
):
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
async def get_alert_config(current_user: TokenData = Depends(get_current_user)):
    """获取告警配置"""
    # 检查管理员权限
    user = await User.get_or_none(id=current_user.user_id)
    if not user or not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")

    config = await _get_alert_config()

    # 获取告警服务状态
    service_config = alert_service.get_config()

    # 构建邮件配置
    email_config_data = config.get("email_config", {})
    email_config = EmailConfig(**email_config_data) if email_config_data else EmailConfig()

    return success(
        AlertConfigResponse(
            channels=AlertChannelConfig(
                feishu_webhooks=[WebhookConfig(**w) for w in config["feishu_webhooks"]],
                dingtalk_webhooks=[WebhookConfig(**w) for w in config["dingtalk_webhooks"]],
                wecom_webhooks=[WebhookConfig(**w) for w in config["wecom_webhooks"]],
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

    # 保存渠道配置
    if request.channels:
        if request.channels.feishu_webhooks is not None:
            webhooks = [w.model_dump() for w in request.channels.feishu_webhooks]
            await _save_alert_config(
                "feishu_webhooks",
                to_json(webhooks),
                "json",
                "飞书 Webhook 配置",
                username,
            )

        if request.channels.dingtalk_webhooks is not None:
            webhooks = [w.model_dump() for w in request.channels.dingtalk_webhooks]
            await _save_alert_config(
                "dingtalk_webhooks",
                to_json(webhooks),
                "json",
                "钉钉 Webhook 配置",
                username,
            )

        if request.channels.wecom_webhooks is not None:
            webhooks = [w.model_dump() for w in request.channels.wecom_webhooks]
            await _save_alert_config(
                "wecom_webhooks",
                to_json(webhooks),
                "json",
                "企业微信 Webhook 配置",
                username,
            )

        if request.channels.email_config is not None:
            email_data = request.channels.email_config.model_dump()
            await _save_alert_config(
                "email_config", to_json(email_data), "json", "邮件告警配置", username
            )

    # 保存自动告警级别
    if request.auto_alert_levels is not None:
        await _save_alert_config(
            "auto_alert_levels",
            ",".join(request.auto_alert_levels),
            "string",
            "自动告警级别",
            username,
        )

    # 保存限流配置
    if request.rate_limit:
        await _save_alert_config(
            "rate_limit_enabled",
            str(request.rate_limit.enabled).lower(),
            "bool",
            "限流启用",
            username,
        )
        await _save_alert_config(
            "rate_limit_window",
            str(request.rate_limit.window),
            "int",
            "限流窗口",
            username,
        )
        await _save_alert_config(
            "rate_limit_max_count",
            str(request.rate_limit.max_count),
            "int",
            "限流次数",
            username,
        )

    # 保存重试配置
    if request.retry:
        await _save_alert_config(
            "retry_enabled",
            str(request.retry.enabled).lower(),
            "bool",
            "重试启用",
            username,
        )
        await _save_alert_config(
            "max_retries",
            str(request.retry.max_retries),
            "int",
            "最大重试次数",
            username,
        )
        await _save_alert_config(
            "retry_delay", str(request.retry.retry_delay), "float", "重试间隔", username
        )

    # 重新加载配置
    await alert_service.reload_config()

    logger.info(f"告警配置已更新 by {username}")

    return success({"updated": True}, message="告警配置已更新")


@router.post(
    "/reload",
    response_model=BaseResponse[dict],
    summary="重新加载告警配置",
    description="重新加载告警配置（管理员）",
)
async def reload_alert_config(current_user: TokenData = Depends(get_current_user)):
    """重新加载告警配置"""
    user = await User.get_or_none(id=current_user.user_id)
    if not user or not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")

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
    current_user: TokenData = Depends(get_current_user),
):
    """获取告警历史"""
    user = await User.get_or_none(id=current_user.user_id)
    if not user or not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")

    history = alert_service.get_history(limit=limit, level=level, source=source)

    return success(
        AlertHistoryResponse(items=[AlertHistoryItem(**h) for h in history], total=len(history))
    )


@router.get(
    "/stats",
    response_model=BaseResponse[AlertStatsResponse],
    summary="获取告警统计",
    description="获取告警统计信息（管理员）",
)
async def get_alert_stats(current_user: TokenData = Depends(get_current_user)):
    """获取告警统计"""
    user = await User.get_or_none(id=current_user.user_id)
    if not user or not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")

    stats = alert_service.get_stats()

    return success(AlertStatsResponse(**stats))


@router.post(
    "/test",
    response_model=BaseResponse[AlertTestResponse],
    summary="发送测试告警",
    description="发送测试告警（管理员）",
)
async def send_test_alert(
    request: AlertTestRequest, current_user: TokenData = Depends(get_current_user)
):
    """发送测试告警"""
    user = await User.get_or_none(id=current_user.user_id)
    if not user or not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")

    result = await alert_service.send_test_alert(channel=request.channel)

    return success(AlertTestResponse(**result))
