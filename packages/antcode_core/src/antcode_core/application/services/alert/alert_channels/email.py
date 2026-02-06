"""邮件告警渠道"""

import asyncio
import smtplib
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from loguru import logger

from antcode_core.application.services.alert.alert_channels.base import AlertChannel


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

        subject = f"[{level}] AntCode 系统告警"

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
            <p><span class="level-badge">{level}</span></p>
            <div class="message">
                <pre>{message}</pre>
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

    async def _send_email(
        self, recipient_email: str, recipient_name: str, subject: str, html_body: str
    ) -> bool:
        """发送单封邮件"""
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = Header(subject, "utf-8")
            msg["From"] = f"{self.sender_name} <{self.smtp_user}>"
            msg["To"] = (
                f"{recipient_name} <{recipient_email}>" if recipient_name else recipient_email
            )

            # 添加HTML内容
            html_part = MIMEText(html_body, "html", "utf-8")
            msg.attach(html_part)

            # 发送邮件
            if self.smtp_ssl:
                server = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=10)
            else:
                server = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=10)
                server.starttls()

            server.login(self.smtp_user, self.smtp_password)
            server.sendmail(self.smtp_user, [recipient_email], msg.as_string())
            server.quit()

            logger.debug(f"邮件告警发送成功: {recipient_email}")
            return True

        except smtplib.SMTPAuthenticationError as e:
            logger.error(f"邮件认证失败: {e}")
            return False
        except smtplib.SMTPException as e:
            logger.error(f"邮件发送失败: {e}")
            return False
        except Exception as e:
            logger.error(f"邮件发送异常: {e}")
            return False

    async def _send_single_alert_with_retry(
        self, recipient: dict, subject: str, html_body: str
    ) -> bool:
        """发送单条告警（带重试）"""
        retries = self.max_retries if self.retry_enabled else 1

        for attempt in range(retries):
            try:
                if attempt > 0:
                    await asyncio.sleep(self.retry_delay * (2 ** (attempt - 1)))

                success = await self._send_email(
                    recipient.get("email", ""),
                    recipient.get("name", ""),
                    subject,
                    html_body,
                )
                if success:
                    return True

            except Exception as e:
                logger.error(f"邮件发送重试失败 (attempt {attempt + 1}): {e}")
                if attempt == retries - 1:
                    return False

        return False

    async def send_alert_with_fallback(
        self, message: str, level: str, default_levels: list[str]
    ) -> bool:
        """发送告警（带级别过滤）"""
        if not self.recipients or not self.smtp_host:
            return False

        subject, html_body = self._build_email_content(message, level)
        tasks = []

        for recipient in self.recipients:
            target_levels = recipient.get("levels", [])

            # 优先级逻辑：收件人配置的级别 > 默认级别
            should_send = (
                (level in target_levels) if target_levels else (level in (default_levels or []))
            )

            if should_send and recipient.get("email"):
                tasks.append(self._send_single_alert_with_retry(recipient, subject, html_body))

        if not tasks:
            return False

        # 并发发送
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 检查是否至少有一个成功
        return any(isinstance(r, bool) and r for r in results)

    async def send_alert_force(self, message: str, level: str) -> bool:
        """强制发送告警（忽略级别过滤）"""
        if not self.recipients or not self.smtp_host:
            return False

        subject, html_body = self._build_email_content(message, level)
        tasks = []

        for recipient in self.recipients:
            if recipient.get("email"):
                tasks.append(self._send_single_alert_with_retry(recipient, subject, html_body))

        if not tasks:
            return False

        results = await asyncio.gather(*tasks, return_exceptions=True)
        return any(isinstance(r, bool) and r for r in results)
