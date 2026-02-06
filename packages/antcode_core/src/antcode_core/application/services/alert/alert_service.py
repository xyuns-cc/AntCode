"""
告警服务 - 支持数据库配置和告警历史记录
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from loguru import logger

from antcode_core.common.serialization import from_json
from antcode_core.application.services.alert.alert_channels import (
    DingtalkAlertChannel,
    EmailAlertChannel,
    FeishuAlertChannel,
    WeComAlertChannel,
)
from antcode_core.application.services.alert.alert_manager import alert_manager


class AlertService:
    """告警服务 - 管理告警配置和发送"""

    def __init__(self):
        self._initialized = False
        self._config_cache = {}
        self._alert_history = []  # 内存中的告警历史
        self._max_history = 1000  # 最大历史记录数

    async def initialize(self):
        """初始化告警服务"""
        if self._initialized:
            return

        try:
            # 从数据库加载配置
            config = await self._load_config_from_db()
            if config:
                await self._apply_config(config)
                self._config_cache = config

            self._initialized = True
            logger.info("告警服务初始化完成")
        except Exception as e:
            logger.error(f"告警服务初始化失败: {e}")

    async def _load_config_from_db(self):
        """从数据库加载告警配置"""
        try:
            from antcode_core.domain.models import SystemConfig

            config = {
                "feishu_webhooks": [],
                "dingtalk_webhooks": [],
                "wecom_webhooks": [],
                "email_config": {},  # 邮件配置
                "auto_alert_levels": ["ERROR", "CRITICAL"],
                "rate_limit_enabled": True,
                "rate_limit_window": 60,
                "rate_limit_max_count": 3,
                "retry_enabled": True,
                "max_retries": 3,
                "retry_delay": 1.0,
            }

            # 查询告警相关配置
            configs = await SystemConfig.filter(category="alert", is_active=True).all()

            for cfg in configs:
                key = cfg.config_key
                value = cfg.config_value

                if key == "feishu_webhooks":
                    config["feishu_webhooks"] = self._parse_webhooks(value)
                elif key == "dingtalk_webhooks":
                    config["dingtalk_webhooks"] = self._parse_webhooks(value)
                elif key == "wecom_webhooks":
                    config["wecom_webhooks"] = self._parse_webhooks(value)
                elif key == "email_config":
                    config["email_config"] = self._parse_webhooks(value) if value else {}
                elif key == "auto_alert_levels":
                    config["auto_alert_levels"] = [
                        level.strip() for level in value.split(",") if level.strip()
                    ]
                elif key == "rate_limit_enabled":
                    config["rate_limit_enabled"] = value.lower() in ("true", "1", "yes")
                elif key == "rate_limit_window":
                    config["rate_limit_window"] = int(value)
                elif key == "rate_limit_max_count":
                    config["rate_limit_max_count"] = int(value)
                elif key == "retry_enabled":
                    config["retry_enabled"] = value.lower() in ("true", "1", "yes")
                elif key == "max_retries":
                    config["max_retries"] = int(value)
                elif key == "retry_delay":
                    config["retry_delay"] = float(value)

            return config
        except Exception as e:
            logger.warning(f"从数据库加载告警配置失败: {e}")
            return {}

    def _parse_webhooks(self, value):
        """解析 Webhook 配置 JSON"""
        try:
            if not value:
                return []
            return from_json(value)
        except Exception:
            return []

    async def _apply_config(self, config):
        """应用告警配置"""
        # 启用异步发送
        alert_manager.configure_async()

        # 配置限流
        alert_manager.configure_rate_limit(
            enabled=config.get("rate_limit_enabled", True),
            window=config.get("rate_limit_window", 60),
            max_count=config.get("rate_limit_max_count", 3),
        )

        # 清除现有渠道
        for channel_name in alert_manager.get_available_channels():
            alert_manager.remove_channel(channel_name)

        # 配置飞书
        feishu_webhooks = config.get("feishu_webhooks", [])
        if feishu_webhooks:
            channel = FeishuAlertChannel(feishu_webhooks)
            channel.configure_retry(
                config.get("retry_enabled", True),
                config.get("max_retries", 3),
                config.get("retry_delay", 1.0),
            )
            alert_manager.add_channel(channel, enabled=True)

        # 配置钉钉
        dingtalk_webhooks = config.get("dingtalk_webhooks", [])
        if dingtalk_webhooks:
            channel = DingtalkAlertChannel(dingtalk_webhooks)
            channel.configure_retry(
                config.get("retry_enabled", True),
                config.get("max_retries", 3),
                config.get("retry_delay", 1.0),
            )
            alert_manager.add_channel(channel, enabled=True)

        # 配置企业微信
        wecom_webhooks = config.get("wecom_webhooks", [])
        if wecom_webhooks:
            channel = WeComAlertChannel(wecom_webhooks)
            channel.configure_retry(
                config.get("retry_enabled", True),
                config.get("max_retries", 3),
                config.get("retry_delay", 1.0),
            )
            alert_manager.add_channel(channel, enabled=True)

        # 配置邮件
        email_config = config.get("email_config", {})
        if email_config and email_config.get("smtp_host"):
            channel = EmailAlertChannel(email_config)
            channel.configure_retry(
                config.get("retry_enabled", True),
                config.get("max_retries", 3),
                config.get("retry_delay", 1.0),
            )
            alert_manager.add_channel(channel, enabled=True)
            email_recipients = len(email_config.get("recipients", []))
        else:
            email_recipients = 0

        logger.info(
            f"告警配置已应用: 飞书={len(feishu_webhooks)}, 钉钉={len(dingtalk_webhooks)}, 企微={len(wecom_webhooks)}, 邮件={email_recipients}"
        )

    async def reload_config(self):
        """重新加载配置"""
        config = await self._load_config_from_db()
        if config:
            await self._apply_config(config)
            self._config_cache = config
            logger.info("告警配置已重新加载")

    async def send_alert(self, message, level="ERROR", source="system", extra=None):
        """
        发送告警

        Args:
            message: 告警消息
            level: 告警级别 (ERROR, CRITICAL, WARNING, INFO)
            source: 告警来源 (system, task, worker, scheduler)
            extra: 额外信息

        Returns:
            发送结果
        """
        if not self._initialized:
            await self.initialize()

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

        # 发送告警
        result = alert_manager.send_alert(formatted_message, level)

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

    async def send_test_alert(self, channel="all", timeout: float = 15.0):
        """发送测试告警（带超时保护）"""
        if not self._initialized:
            await self.initialize()

        enabled_channels = alert_manager.get_enabled_channels()
        if not enabled_channels:
            logger.warning("测试告警失败: 没有配置任何告警渠道")
            return {
                "success": False,
                "message": "没有配置任何告警渠道，请先添加飞书/钉钉/企业微信 Webhook 或邮件配置",
                "result": {"enabled_channels": []},
            }

        message = f"这是一条测试告警消息 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

        async def send_to_channel(channel_name: str) -> tuple[str, bool, str | None]:
            """发送到单个渠道，返回 (渠道名, 是否成功, 错误信息)"""
            channel_obj = alert_manager._channels.get(channel_name)
            if not channel_obj:
                return channel_name, False, "渠道不存在"

            try:
                result = await channel_obj.send_alert_force(message, "INFO")
                if result:
                    logger.info(f"测试告警发送成功: {channel_name}")
                    return channel_name, True, None
                else:
                    logger.warning(f"测试告警发送失败: {channel_name}")
                    return channel_name, False, "发送失败"
            except Exception as e:
                logger.error(f"测试告警发送异常 [{channel_name}]: {e}")
                return channel_name, False, str(e)

        try:
            # 并发发送到所有渠道，带超时保护
            tasks = [send_to_channel(ch) for ch in enabled_channels]
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=timeout,
            )

            success_count = 0
            fail_count = 0
            errors = []

            for result in results:
                if isinstance(result, Exception):
                    fail_count += 1
                    errors.append(str(result))
                else:
                    channel_name, success, error = result
                    if success:
                        success_count += 1
                    else:
                        fail_count += 1
                        errors.append(f"{channel_name}: {error}")

            if success_count > 0:
                return {
                    "success": True,
                    "message": f"测试告警已发送: 成功 {success_count}, 失败 {fail_count}",
                    "result": {
                        "enabled_channels": enabled_channels,
                        "success_count": success_count,
                        "fail_count": fail_count,
                        "errors": errors if errors else None,
                    },
                }
            else:
                logger.error(f"测试告警全部失败: {errors}")
                return {
                    "success": False,
                    "message": f"测试告警发送失败: {'; '.join(errors)}",
                    "result": {
                        "enabled_channels": enabled_channels,
                        "success_count": 0,
                        "fail_count": fail_count,
                        "errors": errors,
                    },
                }

        except TimeoutError:
            logger.error(f"测试告警超时 ({timeout}s)")
            return {
                "success": False,
                "message": f"发送超时 ({timeout}s)，请检查网络连接",
                "result": {"error": "timeout"},
            }
        except Exception as e:
            logger.error(f"测试告警异常: {e}")
            return {
                "success": False,
                "message": f"发送异常: {e}",
                "result": {"error": str(e)},
            }

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
        level_counts = {}
        for h in history:
            level = h.get("level", "UNKNOWN")
            level_counts[level] = level_counts.get(level, 0) + 1

        # 按来源统计
        source_counts = {}
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
