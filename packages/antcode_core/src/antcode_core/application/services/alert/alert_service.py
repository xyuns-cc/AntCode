"""
告警服务 - 支持数据库配置和告警历史记录
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
from typing import Any

from loguru import logger

from antcode_core.application.services.alert.alert_channels import (
    DingtalkAlertChannel,
    EmailAlertChannel,
    FeishuAlertChannel,
    WeComAlertChannel,
)
from antcode_core.application.services.alert.alert_manager import alert_manager
from antcode_core.application.services.alert.test_delivery import (
    TestAlertDelivery,
    deliver_test_alert,
    summarize_test_results,
)
from antcode_core.common.serialization import from_json


class AlertService:
    """告警服务 - 管理告警配置和发送"""

    def __init__(self):
        self._initialized = False
        self._config_cache = {}
        self._alert_history = []  # 内存中的告警历史
        self._max_history = 1000  # 最大历史记录数
        self._reload_lock = asyncio.Lock()

    async def initialize(self):
        """初始化告警服务"""
        if self._initialized:
            return
        await self.reload_config()
        logger.info("告警服务初始化完成")

    async def _load_config_from_db(self):
        """从数据库加载告警配置"""
        from antcode_core.domain.models import SystemConfig

        config = self._default_config()
        configs = await SystemConfig.filter(category="alert", is_active=True).all()
        for cfg in configs:
            parsed = self._parse_config_value(cfg.config_key, cfg.config_value)
            if parsed is not None:
                config[cfg.config_key] = parsed
        return config

    @staticmethod
    def _default_config():
        return {
            "feishu_webhooks": [],
            "dingtalk_webhooks": [],
            "wecom_webhooks": [],
            "email_config": {},
            "auto_alert_levels": ["ERROR", "CRITICAL"],
            "rate_limit_enabled": True,
            "rate_limit_window": 60,
            "rate_limit_max_count": 3,
            "retry_enabled": True,
            "max_retries": 3,
            "retry_delay": 1.0,
        }

    def _parse_config_value(self, key, value):
        parsers: dict[str, Callable[[str], Any]] = {
            "feishu_webhooks": self._parse_webhooks,
            "dingtalk_webhooks": self._parse_webhooks,
            "wecom_webhooks": self._parse_webhooks,
            "email_config": lambda raw: self._parse_webhooks(raw) if raw else {},
            "auto_alert_levels": lambda raw: [level.strip() for level in raw.split(",") if level.strip()],
            "rate_limit_enabled": self._parse_boolean,
            "retry_enabled": self._parse_boolean,
            "rate_limit_window": int,
            "rate_limit_max_count": int,
            "max_retries": int,
            "retry_delay": float,
        }
        parser = parsers.get(key)
        return parser(value) if parser else None

    @staticmethod
    def _parse_boolean(value):
        return value.lower() in ("true", "1", "yes")

    def _parse_webhooks(self, value):
        """解析 Webhook 配置 JSON"""
        if not value:
            return []
        return from_json(value)

    async def _apply_config(self, config):
        """应用告警配置"""
        alert_manager.configure_async()
        alert_manager.configure_rate_limit(
            enabled=config.get("rate_limit_enabled", True),
            window=config.get("rate_limit_window", 60),
            max_count=config.get("rate_limit_max_count", 3),
        )
        for channel_name in alert_manager.get_available_channels():
            alert_manager.remove_channel(channel_name)
        channel_specs = (
            ("feishu_webhooks", FeishuAlertChannel),
            ("dingtalk_webhooks", DingtalkAlertChannel),
            ("wecom_webhooks", WeComAlertChannel),
        )
        channel_counts = {}
        for key, channel_type in channel_specs:
            values = config.get(key, [])
            channel_counts[key] = len(values)
            self._configure_channel(channel_type, values, config)
        email_config = config.get("email_config", {})
        email_recipients = self._configure_email_channel(email_config, config)
        logger.info(
            f"告警配置已应用: 飞书={channel_counts['feishu_webhooks']}, "
            f"钉钉={channel_counts['dingtalk_webhooks']}, "
            f"企微={channel_counts['wecom_webhooks']}, 邮件={email_recipients}"
        )

    @staticmethod
    def _configure_channel(channel_type, values, config):
        if not values:
            return
        channel = channel_type(values)
        channel.configure_retry(
            config.get("retry_enabled", True),
            config.get("max_retries", 3),
            config.get("retry_delay", 1.0),
        )
        alert_manager.add_channel(channel, enabled=True)

    def _configure_email_channel(self, email_config, config):
        if not email_config or not email_config.get("smtp_host"):
            return 0
        self._configure_channel(EmailAlertChannel, email_config, config)
        return len(email_config.get("recipients", []))

    async def reload_config(self):
        """重新加载配置"""
        async with self._reload_lock:
            config = await self._load_config_from_db()
            if self._initialized and config == self._config_cache:
                return
            await self._apply_config(config)
            self._config_cache = config
            self._initialized = True
            logger.info("告警配置已重新加载")

    async def send_alert(self, message, level="ERROR", source="system", extra=None, rate_key=None):
        """
        发送告警

        Args:
            message: 告警消息
            level: 告警级别 (ERROR, CRITICAL, WARNING, INFO)
            source: 告警来源 (system, task, worker, scheduler)
            extra: 额外信息
            rate_key: 限流去重键（不传时用 level|source|message；
                消息含瞬时数值时调用方应传稳定键，否则限流不生效）

        Returns:
            发送结果
        """
        # 每次发送前读取 PostgreSQL 权威配置；未变化时不会重建 channel。
        # 该路径不依赖易丢失的跨进程 Pub/Sub 通知。
        await self.reload_config()

        # 格式化消息
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        formatted_message = f"{timestamp} | {level: <8} | {source} | {message}"

        if extra:
            extra_str = " | ".join(f"{k}={v}" for k, v in extra.items())
            formatted_message += f" | {extra_str}"

        # 记录历史
        self._add_history(
            {
                "timestamp": timestamp,
                "level": level,
                "source": source,
                "message": message,
                "extra": extra or {},
                "status": "pending",
            }
        )

        # 发送告警。限流键必须排除时间戳/extra（含 created_at 等易变字段），
        # 否则每条消息哈希唯一，同一告警重复轰炸时限流形同虚设。
        result = alert_manager.send_alert_auto(
            formatted_message,
            level,
            self._config_cache["auto_alert_levels"],
            rate_key=rate_key or f"{level}|{source}|{message}",
        )

        # 更新历史状态
        if self._alert_history:
            self._alert_history[-1]["status"] = result.get("status", "unknown")

        return result

    async def send_task_alert(self, task_id, project_name, error_message, exit_code=None):
        """发送任务失败告警"""
        extra = {
            "task_id": task_id,
            "project": project_name,
        }
        if exit_code is not None:
            extra["exit_code"] = exit_code

        await self.send_alert(
            message=f"任务执行失败: {error_message}",
            level="ERROR",
            source="task",
            extra=extra,
        )

    async def send_worker_alert(self, worker_name, worker_id, alert_type, message):
        """发送 Worker 告警"""
        await self.send_alert(
            message=f"Worker {worker_name}: {message}",
            level="CRITICAL" if alert_type == "offline" else "WARNING",
            source="worker",
            extra={
                "worker_id": worker_id,
                "worker_name": worker_name,
                "alert_type": alert_type,
            },
        )

    async def send_test_alert(
        self,
        channel: str = "all",
        *,
        message: str = "",
        timeout: float = 15.0,
    ):
        """发送测试告警（带超时保护）"""
        if not self._initialized:
            await self.initialize()
        request = TestAlertDelivery(channel=channel, message=message, timeout=timeout)
        return await deliver_test_alert(request, self._send_test_to_channel)

    async def _send_test_to_channel(self, channel_name, message):
        channel_obj = alert_manager._channels.get(channel_name)
        if not channel_obj:
            return channel_name, False, "渠道不存在"
        try:
            success = await channel_obj.send_alert_force(message, "INFO")
        except Exception as exc:
            logger.error(f"测试告警发送异常 [{channel_name}]: {exc}")
            return channel_name, False, str(exc)
        if success:
            logger.info(f"测试告警发送成功: {channel_name}")
            return channel_name, True, None
        logger.warning(f"测试告警发送失败: {channel_name}")
        return channel_name, False, "发送失败"

    _summarize_test_results = staticmethod(summarize_test_results)

    def _add_history(self, record):
        """添加告警历史"""
        self._alert_history.append(record)
        if len(self._alert_history) > self._max_history:
            self._alert_history.pop(0)

    def get_history(self, limit=50, level=None, source=None):
        """获取告警历史"""
        history = self._alert_history.copy()

        if level:
            history = [h for h in history if h.get("level") == level]
        if source:
            history = [h for h in history if h.get("source") == source]

        # 返回最新的记录
        return list(reversed(history[-limit:]))

    def get_config(self):
        """获取当前配置"""
        return {
            **self._config_cache,
            "enabled_channels": alert_manager.get_enabled_channels(),
            "available_channels": alert_manager.get_available_channels(),
            "rate_limit_stats": alert_manager.get_rate_limit_stats(),
        }

    def get_stats(self):
        """获取告警统计"""
        history = self._alert_history

        # 按级别统计
        level_counts: dict[str, int] = {}
        for h in history:
            level = h.get("level", "UNKNOWN")
            level_counts[level] = level_counts.get(level, 0) + 1

        # 按来源统计
        source_counts: dict[str, int] = {}
        for h in history:
            source = h.get("source", "unknown")
            source_counts[source] = source_counts.get(source, 0) + 1

        return {
            "total_alerts": len(history),
            "by_level": level_counts,
            "by_source": source_counts,
            "enabled_channels": alert_manager.get_enabled_channels(),
            "rate_limit_stats": alert_manager.get_rate_limit_stats(),
        }


# 全局实例
alert_service = AlertService()
