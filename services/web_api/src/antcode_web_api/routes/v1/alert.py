"""告警管理 API"""

from __future__ import annotations

from antcode_core.application.services.alert import alert_service
from antcode_core.application.services.audit import audit_service
from antcode_core.common.security.auth import TokenData, get_current_super_admin
from antcode_core.domain.models import User, UserRole
from antcode_core.domain.models.audit_log import AuditAction
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
    WebhookConfig,
)
from fastapi import APIRouter, Depends, Query
from loguru import logger

from antcode_web_api.committed_audit import record_committed_audit
from antcode_web_api.deps import require_role
from antcode_web_api.response import BaseResponse, success
from antcode_web_api.routes.v1 import alert_config_store

_get_alert_config = alert_config_store.get_alert_config
_mask_webhooks = alert_config_store.mask_webhooks
_masked_email = alert_config_store.masked_email
_save_alert_config = alert_config_store.save_alert_config
_updated_alert_fields = alert_config_store.updated_alert_fields

_REQUIRE_ADMIN = require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN)

router = APIRouter()


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
            value=",".join(request.auto_alert_levels),
            value_type="string",
            description="自动告警级别",
            username=username,
        )
    if request.rate_limit:
        await _save_rate_limit_config(request.rate_limit, username)
    if request.retry:
        await _save_retry_config(request.retry, username)
    # notify=True：本进程之外还有别的 uvicorn worker（SERVER_WORKERS>1），
    # 不广播的话它们会一直用旧渠道回答 /config 与 /test。
    await alert_service.reload_config(notify=True)
    await record_committed_audit(
        "alert_config_update",
        lambda: audit_service.log(
            action=AuditAction.ALERT_CONFIG_UPDATE,
            resource_type="alert_config",
            username=username,
            user_id=current_user.user_id,
            description="更新告警配置",
            new_value={"updated_fields": _updated_alert_fields(request)},
        ),
    )
    logger.info(f"告警配置已更新 by {username}")
    return success({"updated": True}, message="告警配置已更新")


async def _save_channel_config(channels, existing: dict, username: str) -> None:
    await alert_config_store.save_channel_config(channels, existing, username, save_config=_save_alert_config)


async def _save_rate_limit_config(config, username: str) -> None:
    await alert_config_store.save_rate_limit_config(config, username, save_config=_save_alert_config)


async def _save_retry_config(config, username: str) -> None:
    await alert_config_store.save_retry_config(config, username, save_config=_save_alert_config)


@router.post(
    "/reload",
    response_model=BaseResponse[dict],
    summary="重新加载告警配置",
    description="重新加载告警配置（管理员）",
)
async def reload_alert_config(_admin: User = Depends(_REQUIRE_ADMIN)):
    """重新加载告警配置"""
    await alert_service.reload_config(notify=True)

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
    result = await alert_service.send_test_alert(channel=request.channel, message=request.message)

    return success(AlertTestResponse(**result))
