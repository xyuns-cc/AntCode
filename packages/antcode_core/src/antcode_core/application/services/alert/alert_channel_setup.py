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
    """按配置重建全部渠道。

    先把新拓扑整份装配好、最后一次性换入，而不是先清空再逐个装回：清空到装回
    之间那段时间里，发送侧（跑在 alert_manager 自己的线程上）会读到一个空拓扑
    并把落在窗口里的告警判成"没有配置任何渠道"。删掉的 Webhook 同样不会残留
    ——整份替换本来就不保留旧键。
    """
    alert_manager.configure_async()
    alert_manager.configure_rate_limit(
        enabled=config.get("rate_limit_enabled", True),
        window=config.get("rate_limit_window", DEFAULT_RATE_LIMIT_WINDOW),
        max_count=config.get("rate_limit_max_count", DEFAULT_RATE_LIMIT_MAX_COUNT),
    )

    channels: list = []
    summary = []
    for key, channel_type, label in WEBHOOK_CHANNEL_SPECS:
        values = config.get(key, [])
        channels.extend(_build_channel(channel_type, values, config))
        summary.append(f"{label}={len(values)}")
    email_channels, recipient_count = _build_email_channel(config)
    channels.extend(email_channels)
    summary.append(f"邮件={recipient_count}")

    alert_manager.replace_channels(channels)
    logger.info(f"告警配置已应用: {', '.join(summary)}")


def _build_channel(channel_type, values, config: dict) -> list:
    """没有目标就不装配这条渠道；回列表让调用方直接拼进拓扑。"""
    if not values:
        return []
    channel = channel_type(values)
    channel.configure_retry(
        config.get("retry_enabled", True),
        config.get("max_retries", DEFAULT_MAX_RETRIES),
        config.get("retry_delay", DEFAULT_RETRY_DELAY),
    )
    return [channel]


def _build_email_channel(config: dict) -> tuple[list, int]:
    email_config = config.get("email_config", {})
    if not email_config or not email_config.get("smtp_host"):
        return [], 0
    channels = _build_channel(EmailAlertChannel, email_config, config)
    return channels, len(email_config.get("recipients", []))


__all__ = ["WEBHOOK_CHANNEL_SPECS", "apply_alert_config"]
