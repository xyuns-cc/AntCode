"""把一份告警配置翻译成 alert_manager 的渠道拓扑。

从 alert_service 拆出：「配置字典 → 渠道实例」的装配规则和「告警服务编排」是两
件事，且 alert_service.py 已顶到 300 行硬上限。
"""

from __future__ import annotations

from loguru import logger

from antcode_core.application.services.alert.alert_channels import (
    DingtalkAlertChannel,
    EmailAlertChannel,
    FeishuAlertChannel,
    WeComAlertChannel,
)
from antcode_core.application.services.alert.alert_manager import alert_manager

DEFAULT_RATE_LIMIT_WINDOW = 60
DEFAULT_RATE_LIMIT_MAX_COUNT = 3
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY = 1.0

WEBHOOK_CHANNEL_SPECS = (
    ("feishu_webhooks", FeishuAlertChannel, "飞书"),
    ("dingtalk_webhooks", DingtalkAlertChannel, "钉钉"),
    ("wecom_webhooks", WeComAlertChannel, "企微"),
)


def apply_alert_config(config: dict) -> None:
    """按配置重建全部渠道。先清空再装配，避免删掉的 Webhook 残留在进程内。"""
    alert_manager.configure_async()
    alert_manager.configure_rate_limit(
        enabled=config.get("rate_limit_enabled", True),
        window=config.get("rate_limit_window", DEFAULT_RATE_LIMIT_WINDOW),
        max_count=config.get("rate_limit_max_count", DEFAULT_RATE_LIMIT_MAX_COUNT),
    )
    for channel_name in alert_manager.get_available_channels():
        alert_manager.remove_channel(channel_name)

    summary = []
    for key, channel_type, label in WEBHOOK_CHANNEL_SPECS:
        values = config.get(key, [])
        _configure_channel(channel_type, values, config)
        summary.append(f"{label}={len(values)}")
    summary.append(f"邮件={_configure_email_channel(config)}")
    logger.info(f"告警配置已应用: {', '.join(summary)}")


def _configure_channel(channel_type, values, config: dict) -> None:
    if not values:
        return
    channel = channel_type(values)
    channel.configure_retry(
        config.get("retry_enabled", True),
        config.get("max_retries", DEFAULT_MAX_RETRIES),
        config.get("retry_delay", DEFAULT_RETRY_DELAY),
    )
    alert_manager.add_channel(channel, enabled=True)


def _configure_email_channel(config: dict) -> int:
    email_config = config.get("email_config", {})
    if not email_config or not email_config.get("smtp_host"):
        return 0
    _configure_channel(EmailAlertChannel, email_config, config)
    return len(email_config.get("recipients", []))


__all__ = ["WEBHOOK_CHANNEL_SPECS", "apply_alert_config"]
