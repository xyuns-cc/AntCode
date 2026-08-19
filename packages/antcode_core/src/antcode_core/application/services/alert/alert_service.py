"""
告警服务 - 支持数据库配置和告警历史记录
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
from typing import Any

from loguru import logger

from antcode_core.application.services.alert.alert_channel_setup import apply_alert_config
from antcode_core.application.services.alert.alert_config_broadcast import (
    publish_alert_config_invalidation,
    start_alert_config_subscriber,
)
from antcode_core.application.services.alert.alert_history import (
    DEFAULT_HISTORY_LIMIT,
    AlertHistory,
)
from antcode_core.application.services.alert.alert_manager import alert_manager
from antcode_core.application.services.alert.test_delivery import (
    TestAlertDelivery,
    deliver_test_alert,
    summarize_test_results,
)
from antcode_core.common.serialization import from_json

_UNKNOWN_LEVEL = "UNKNOWN"
_UNKNOWN_SOURCE = "unknown"


class AlertService:
    """告警服务 - 管理告警配置和发送"""

    def __init__(self):
        self._initialized = False
        self._subscribed = False
        self._config_cache = {}
        self._history = AlertHistory()
        self._reload_lock = asyncio.Lock()

    async def initialize(self):
        """初始化告警服务。

        订阅跨进程失效通道放在这里、而不是各服务的启动脚本里：SERVER_WORKERS>1
        时每个 uvicorn worker 都是独立进程、各持一份 alert_manager 单例，任何一个
        进程漏订阅就会一直用旧渠道回答请求。收拢到一处才不会有人忘了接。
        """
        if not self._subscribed:
            await self.start_invalidation_subscriber()
            self._subscribed = True
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
        apply_alert_config(config)

    async def reload_config(self, notify: bool = False):
        """重新加载配置。

        ``notify=True`` 只由写入方（PUT /alert/config、POST /alert/reload）传，
        且**无条件**广播：本进程可能已经在 send_alert 路径上提前重载过，若按
        "本进程有变化才广播"就会漏掉对兄弟 worker 进程的通知。订阅端回调走
        默认的 ``notify=False``，否则通知会被打回去形成回环。
        """
        async with self._reload_lock:
            await self._reload_from_db()
        if notify:
            await publish_alert_config_invalidation()

    async def _reload_from_db(self):
        config = await self._load_config_from_db()
        if self._initialized and config == self._config_cache:
            return
        await self._apply_config(config)
        self._config_cache = config
        self._initialized = True
        logger.info("告警配置已重新加载")

    async def start_invalidation_subscriber(self):
        """订阅跨进程配置失效。每个进程（含每个 uvicorn worker）启动时调一次。"""
        await start_alert_config_subscriber(self.reload_config)

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
        self._history.add(
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

        # 直接取 status（而非 .get 兜个 "unknown"）：投递结果契约要求每条返回都
        # 带 status，少了就该在测试里炸出来，不该退化成一个查不出源头的历史值。
        self._history.mark_last_status(result["status"])

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
        # 以 PostgreSQL 为准重建渠道，而不是只在未初始化时懒加载：多进程下本进程
        # 可能没处理过配置写入，沿用进程内旧渠道会让同一次点击在"已投递"和
        # "没有配置任何告警渠道"之间随机跳（实测 10 次 4 跳）。
        await self.reload_config()
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

    def get_history(self, limit=DEFAULT_HISTORY_LIMIT, level=None, source=None):
        """获取告警历史"""
        return self._history.recent(limit=limit, level=level, source=source)

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
        return {
            "total_alerts": len(self._history),
            "by_level": self._history.counts_by("level", unknown=_UNKNOWN_LEVEL),
            "by_source": self._history.counts_by("source", unknown=_UNKNOWN_SOURCE),
            "enabled_channels": alert_manager.get_enabled_channels(),
            "rate_limit_stats": alert_manager.get_rate_limit_stats(),
        }


# 全局实例
alert_service = AlertService()
