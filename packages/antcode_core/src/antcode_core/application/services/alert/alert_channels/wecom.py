"""企业微信告警渠道"""

from antcode_core.application.services.alert.alert_channels.base import MultiWebhookChannel


class WeComAlertChannel(MultiWebhookChannel):
    """企业微信告警渠道"""

    def _build_payload(self, message, level):
        """构建企业微信消息载荷"""
        return {"msgtype": "text", "text": {"content": f"[{level}] {message}"}}

    def _check_response(self, data):
        """检查企业微信响应"""
        try:
            if data.get("errcode") == 0:
                return True, ""
            return False, data.get("errmsg", str(data))
        except (KeyError, TypeError, AttributeError):
            return False, "响应解析失败"

    @property
    def channel_name(self):
        return "wecom"
