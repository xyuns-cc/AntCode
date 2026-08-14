"""告警配置 Schema"""

from typing import Literal

from pydantic import BaseModel, Field


class WebhookConfig(BaseModel):
    """Webhook 配置"""

    name: str = Field(..., description="Webhook 名称")
    url: str = Field(..., description="Webhook URL")
    levels: list[str] = Field(default_factory=lambda: ["ERROR", "CRITICAL"], description="告警级别")
    enabled: bool = Field(default=True, description="是否启用")


class EmailRecipient(BaseModel):
    """邮件收件人配置"""

    email: str = Field(..., description="收件人邮箱")
    name: str = Field(default="", description="收件人名称")
    levels: list[str] = Field(default_factory=lambda: ["ERROR", "CRITICAL"], description="告警级别")


class EmailConfig(BaseModel):
    """邮件告警配置"""

    smtp_host: str = Field(default="", max_length=253, description="SMTP服务器地址")
    smtp_port: int = Field(default=465, description="SMTP端口")
    smtp_user: str = Field(default="", description="SMTP用户名")
    smtp_password: str = Field(default="", description="SMTP密码")
    smtp_ssl: bool = Field(default=True, description="是否使用SSL")
    sender_name: str = Field(default="AntCode告警系统", description="发件人名称")
    recipients: list[EmailRecipient] = Field(default_factory=list, description="收件人列表")


class AlertChannelConfig(BaseModel):
    """告警渠道配置"""

    feishu_webhooks: list[WebhookConfig] = Field(default_factory=list, description="飞书 Webhook 列表")
    dingtalk_webhooks: list[WebhookConfig] = Field(default_factory=list, description="钉钉 Webhook 列表")
    wecom_webhooks: list[WebhookConfig] = Field(default_factory=list, description="企业微信 Webhook 列表")
    email_config: EmailConfig = Field(default_factory=EmailConfig, description="邮件告警配置")


class EmailConfigUpdate(BaseModel):
    """邮件渠道部分更新。未提交字段必须保留数据库现值。"""

    smtp_host: str = Field(default="", max_length=253)
    smtp_port: int = Field(default=465, ge=1, le=65535)
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_ssl: bool = True
    sender_name: str = "AntCode告警系统"
    recipients: list[EmailRecipient] = Field(default_factory=list)


class AlertChannelUpdate(BaseModel):
    """渠道部分更新。Webhook 列表出现时代表完整替换该渠道。"""

    feishu_webhooks: list[WebhookConfig] | None = None
    dingtalk_webhooks: list[WebhookConfig] | None = None
    wecom_webhooks: list[WebhookConfig] | None = None
    email_config: EmailConfigUpdate | None = None


class AlertRateLimitConfig(BaseModel):
    """告警限流配置"""

    enabled: bool = Field(default=True, description="是否启用限流")
    window: int = Field(default=60, ge=10, le=3600, description="限流窗口（秒）")
    max_count: int = Field(default=3, ge=1, le=100, description="窗口内最大告警数")


class AlertRetryConfig(BaseModel):
    """告警重试配置"""

    enabled: bool = Field(default=True, description="是否启用重试")
    max_retries: int = Field(default=3, ge=1, le=10, description="最大重试次数")
    retry_delay: float = Field(default=1.0, ge=0.1, le=60, description="重试间隔（秒）")


class AlertRateLimitUpdate(BaseModel):
    """限流部分更新。"""

    enabled: bool = True
    window: int = Field(default=60, ge=10, le=3600)
    max_count: int = Field(default=3, ge=1, le=100)


class AlertRetryUpdate(BaseModel):
    """重试部分更新。"""

    enabled: bool = True
    max_retries: int = Field(default=3, ge=1, le=10)
    retry_delay: float = Field(default=1.0, ge=0.1, le=60)


class AlertConfigRequest(BaseModel):
    """告警配置请求"""

    channels: AlertChannelUpdate | None = None
    auto_alert_levels: list[str] | None = Field(default=None, description="自动告警级别")
    rate_limit: AlertRateLimitUpdate | None = None
    retry: AlertRetryUpdate | None = None


class AlertConfigResponse(BaseModel):
    """告警配置响应"""

    channels: AlertChannelConfig
    auto_alert_levels: list[str]
    rate_limit: AlertRateLimitConfig
    retry: AlertRetryConfig
    enabled_channels: list[str]
    available_channels: list[str]


class AlertHistoryItem(BaseModel):
    """告警历史记录"""

    timestamp: str
    level: str
    source: str
    message: str
    extra: dict = Field(default_factory=dict)
    status: str


class AlertHistoryResponse(BaseModel):
    """告警历史响应"""

    items: list[AlertHistoryItem]
    total: int


class AlertStatsResponse(BaseModel):
    """告警统计响应"""

    total_alerts: int
    by_level: dict
    by_source: dict
    enabled_channels: list[str]
    rate_limit_stats: dict = Field(default_factory=dict)


class AlertTestRequest(BaseModel):
    """测试告警请求"""

    channel: Literal["all", "feishu", "dingtalk", "wecom", "email"] = Field(
        default="all",
        description="测试渠道",
    )
    message: str = Field(default="", description="自定义测试消息")


class AlertTestResponse(BaseModel):
    """测试告警响应"""

    success: bool
    message: str
    result: dict = Field(default_factory=dict)
