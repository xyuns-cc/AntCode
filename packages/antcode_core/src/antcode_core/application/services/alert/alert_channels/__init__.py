"""告警渠道模块"""

from antcode_core.application.services.alert.alert_channels.base import AlertChannel, MultiWebhookChannel
from antcode_core.application.services.alert.alert_channels.dingtalk import DingtalkAlertChannel
from antcode_core.application.services.alert.alert_channels.email import EmailAlertChannel
from antcode_core.application.services.alert.alert_channels.feishu import FeishuAlertChannel
from antcode_core.application.services.alert.alert_channels.wecom import WeComAlertChannel

__all__ = [
    "AlertChannel",
    "MultiWebhookChannel",
    "FeishuAlertChannel",
    "DingtalkAlertChannel",
    "WeComAlertChannel",
    "EmailAlertChannel",
]
