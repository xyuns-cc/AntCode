"""钉钉告警渠道"""

from antcode_core.application.services.alert.alert_channels.base import MultiWebhookChannel


class DingtalkAlertChannel(MultiWebhookChannel):
    """钉钉告警渠道"""

    def _build_payload(self, message, level):
        """构建钉钉消息载荷"""
        return {
            "msgtype": "text",
            "text": {"content": f"[{level}] {message}"},
            "at": {"isAtAll": False},
        }

    def _check_response(self, data):
        """检查钉钉响应"""
        try:
            if data.get("errcode") == 0:
                return True, ""
            return False, data.get("errmsg", str(data))
        except (KeyError, TypeError, AttributeError):
            return False, "响应解析失败"

    @property
    def channel_name(self):
        return "dingtalk"
