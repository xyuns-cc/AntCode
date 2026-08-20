"""邮件告警渠道"""

import asyncio
import html
import smtplib
import ssl
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from loguru import logger

from antcode_core.application.services.alert.alert_channels.base import AlertChannel
from antcode_core.application.services.alert.alert_delivery_status import (
    ERROR_CHANNEL_LEVEL_FILTERED,
    ERROR_CHANNEL_NO_TARGET,
    ERROR_CHANNEL_SMTP,
    ERROR_CHANNEL_SMTP_AUTH,
    ERROR_CHANNEL_UNEXPECTED,
    ERROR_CHANNEL_URL_REJECTED,
    ChannelSendOutcome,
    channel_failed,
    channel_sent,
    merge_channel_outcomes,
)
from antcode_core.application.services.alert.smtp_delivery import (
    SMTPDeliveryConfig,
    deliver_smtp_message,
)


def _clean_header(value: str) -> str:
    """清理邮件头字段，防止 CR/LF 注入额外头部。"""
    return str(value).replace("\r", " ").replace("\n", " ").strip()


class EmailAlertChannel(AlertChannel):
    """邮件告警渠道"""

    def __init__(self, config: dict):
        """
        初始化邮件告警渠道

        Args:
            config: 邮件配置
                - smtp_host: SMTP服务器地址
                - smtp_port: SMTP端口
                - smtp_user: SMTP用户名
                - smtp_password: SMTP密码
                - smtp_ssl: 是否使用SSL
                - sender_name: 发件人名称
                - recipients: 收件人列表 [{"email": "xxx@xxx.com", "name": "xxx", "levels": ["ERROR"]}]
        """
        super().__init__()
        self.smtp_host = config.get("smtp_host", "")
        self.smtp_port = config.get("smtp_port", 465)
        self.smtp_user = config.get("smtp_user", "")
        self.smtp_password = config.get("smtp_password", "")
        self.smtp_ssl = config.get("smtp_ssl", True)
        self.sender_name = config.get("sender_name", "AntCode告警系统")
        self.recipients = config.get("recipients", [])

    @property
    def channel_name(self) -> str:
        return "email"

    def _build_email_content(self, message: str, level: str) -> tuple[str, str]:
        """构建邮件内容"""
        # 级别颜色映射
        level_colors = {
            "DEBUG": "#6c757d",
            "INFO": "#17a2b8",
            "WARNING": "#ffc107",
            "ERROR": "#dc3545",
            "CRITICAL": "#6f42c1",
        }

        color = level_colors.get(level, "#6c757d")

        # 消息中包含用户可控内容（任务名/项目名/错误信息），必须转义后
        # 才能进 HTML，否则可注入任意 HTML/JS 到收件人邮箱。
        safe_level = html.escape(level)
        safe_message = html.escape(message)

        subject = f"[{_clean_header(level)}] AntCode 系统告警"

        html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: {color}; color: white; padding: 15px 20px; border-radius: 8px 8px 0 0; }}
        .header h2 {{ margin: 0; font-size: 18px; }}
        .content {{ background: #f8f9fa; padding: 20px; border: 1px solid #dee2e6; border-top: none; border-radius: 0 0 8px 8px; }}
        .level-badge {{ display: inline-block; background: {color}; color: white; padding: 4px 12px; border-radius: 4px; font-size: 12px; font-weight: bold; }}
        .message {{ background: white; padding: 15px; border-radius: 4px; margin-top: 15px; border-left: 4px solid {color}; }}
        .message pre {{ margin: 0; white-space: pre-wrap; word-wrap: break-word; font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace; font-size: 13px; }}
        .footer {{ margin-top: 20px; padding-top: 15px; border-top: 1px solid #dee2e6; color: #6c757d; font-size: 12px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h2>AntCode 系统告警</h2>
        </div>
        <div class="content">
            <p><span class="level-badge">{safe_level}</span></p>
            <div class="message">
                <pre>{safe_message}</pre>
            </div>
            <div class="footer">
                <p>此邮件由 AntCode 告警系统自动发送，请勿直接回复。</p>
            </div>
        </div>
    </div>
</body>
</html>
"""
        return subject, html_body

    def _delivery_config(self) -> SMTPDeliveryConfig:
        return SMTPDeliveryConfig(
            host=self.smtp_host,
            port=self.smtp_port,
            username=self.smtp_user,
            password=self.smtp_password,
            use_ssl=self.smtp_ssl,
        )

    def _build_message(
        self,
        *,
        recipient_email: str,
        recipient_name: str,
        subject: str,
        html_body: str,
    ) -> tuple[str, str]:
        recipient_email = _clean_header(recipient_email)
        recipient_name = _clean_header(recipient_name)
        sender_name = _clean_header(self.sender_name)
        smtp_user = _clean_header(self.smtp_user)
        message = MIMEMultipart("alternative")
        message["Subject"] = str(Header(subject, "utf-8"))
        message["From"] = f"{sender_name} <{smtp_user}>"
        message["To"] = f"{recipient_name} <{recipient_email}>" if recipient_name else recipient_email
        message.attach(MIMEText(html_body, "html", "utf-8"))
        return recipient_email, message.as_string()

    async def _send_email(
        self,
        *,
        recipient_email: str,
        recipient_name: str,
        subject: str,
        html_body: str,
    ) -> ChannelSendOutcome:
        """发送单封邮件。失败时把 SMTP 服务端原文放进 detail，只给人看。"""
        try:
            recipient_email, message = self._build_message(
                recipient_email=recipient_email,
                recipient_name=recipient_name,
                subject=subject,
                html_body=html_body,
            )
            await asyncio.to_thread(
                deliver_smtp_message,
                self._delivery_config(),
                recipient_email=recipient_email,
                message=message,
            )
        except ValueError as exc:
            logger.error("拒绝连接 SMTP 目标: {}", exc)
            return channel_failed(ERROR_CHANNEL_URL_REJECTED, detail=str(exc))
        except smtplib.SMTPAuthenticationError as exc:
            logger.error("邮件认证失败: {}", exc)
            return channel_failed(ERROR_CHANNEL_SMTP_AUTH, detail=str(exc))
        except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
            logger.error("邮件发送失败: {}", exc)
            return channel_failed(ERROR_CHANNEL_SMTP, detail=str(exc))
        logger.debug("邮件告警发送成功: {}", recipient_email)
        return channel_sent()

    async def _send_single_alert_with_retry(self, recipient: dict, subject: str, html_body: str) -> ChannelSendOutcome:
        """发送单条告警（带重试）。失败时回传最后一次尝试的结构化原因。"""
        # 与 MultiWebhookChannel 一致：先发一次再补 retries-1 次，避免用占位
        # outcome 起头而在 retries<=0 时返回一个谁也没产生过的假原因。
        retries = self.max_retries if self.retry_enabled else 1
        outcome = await self._attempt_send(recipient, subject, html_body)
        for attempt in range(1, retries):
            if outcome.ok:
                return outcome
            await asyncio.sleep(self.retry_delay * (2 ** (attempt - 1)))
            outcome = await self._attempt_send(recipient, subject, html_body)
        return outcome

    async def _attempt_send(self, recipient: dict, subject: str, html_body: str) -> ChannelSendOutcome:
        try:
            return await self._send_email(
                recipient_email=recipient.get("email", ""),
                recipient_name=recipient.get("name", ""),
                subject=subject,
                html_body=html_body,
            )
        except Exception as exc:
            logger.error("邮件发送未预期异常: {}", exc)
            return channel_failed(ERROR_CHANNEL_UNEXPECTED, detail=str(exc))

    async def send_alert_for_level(self, message: str, level: str, default_levels: list[str]) -> ChannelSendOutcome:
        """发送告警（带级别过滤）。优先级：收件人配置的级别 > 默认级别。"""
        allowed_levels = default_levels or []

        def should_send(target_levels: list[str]) -> bool:
            return (level in target_levels) if target_levels else (level in allowed_levels)

        return await self._dispatch(message, level, should_send)

    async def send_alert_force(self, message: str, level: str) -> ChannelSendOutcome:
        """强制发送告警（忽略级别过滤）"""
        return await self._dispatch(message, level, lambda _target_levels: True)

    async def _dispatch(self, message: str, level: str, should_send) -> ChannelSendOutcome:
        if not self.smtp_host:
            return channel_failed(ERROR_CHANNEL_NO_TARGET, detail="未配置 SMTP 服务器地址")
        if not self.recipients:
            return channel_failed(ERROR_CHANNEL_NO_TARGET, detail="未配置任何收件人")

        targets = [
            recipient
            for recipient in self.recipients
            if recipient.get("email") and should_send(recipient.get("levels", []))
        ]
        if not targets:
            return channel_failed(ERROR_CHANNEL_LEVEL_FILTERED, detail=f"没有订阅 {level} 级别的收件人")

        subject, html_body = self._build_email_content(message, level)
        names = [recipient.get("email", "") for recipient in targets]
        outcomes = await asyncio.gather(
            *(self._send_single_alert_with_retry(recipient, subject, html_body) for recipient in targets),
            return_exceptions=True,
        )
        return merge_channel_outcomes(zip(names, outcomes, strict=True))
