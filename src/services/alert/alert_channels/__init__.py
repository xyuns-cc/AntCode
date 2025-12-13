# logger/alert_channels/__init__.py
"""告警渠道模块"""
from src.services.alert.alert_channels.base import AlertChannel, MultiWebhookChannel
from src.services.alert.alert_channels.feishu import FeishuAlertChannel
from src.services.alert.alert_channels.dingtalk import DingtalkAlertChannel
from src.services.alert.alert_channels.wecom import WeComAlertChannel
from src.services.alert.alert_channels.email import EmailAlertChannel

__all__ = [
    'AlertChannel',
    'MultiWebhookChannel',
    'FeishuAlertChannel',
    'DingtalkAlertChannel',
    'WeComAlertChannel',
    'EmailAlertChannel'
]

