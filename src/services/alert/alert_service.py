"""
告警服务 - 支持数据库配置和告警历史记录
"""
from datetime import datetime
from typing import Optional, List, Dict, Any
from loguru import logger

from src.services.alert.alert_manager import alert_manager
from src.services.alert.alert_channels import FeishuAlertChannel, DingtalkAlertChannel, WeComAlertChannel, EmailAlertChannel


class AlertService:
    """告警服务 - 管理告警配置和发送"""

    def __init__(self):
        self._initialized = False
        self._config_cache: Dict[str, Any] = {}
        self._alert_history: List[Dict] = []  # 内存中的告警历史
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

    async def _load_config_from_db(self) -> Dict[str, Any]:
        """从数据库加载告警配置"""
        try:
            from src.models import SystemConfig

            config = {
                'feishu_webhooks': [],
                'dingtalk_webhooks': [],
                'wecom_webhooks': [],
                'email_config': {},  # 邮件配置
                'auto_alert_levels': ['ERROR', 'CRITICAL'],
                'rate_limit_enabled': True,
                'rate_limit_window': 60,
                'rate_limit_max_count': 3,
                'retry_enabled': True,
                'max_retries': 3,
                'retry_delay': 1.0,
            }

            # 查询告警相关配置
            configs = await SystemConfig.filter(
                category='alert',
                is_active=True
            ).all()

            for cfg in configs:
                key = cfg.config_key
                value = cfg.config_value

                if key == 'feishu_webhooks':
                    config['feishu_webhooks'] = self._parse_webhooks(value)
                elif key == 'dingtalk_webhooks':
                    config['dingtalk_webhooks'] = self._parse_webhooks(value)
                elif key == 'wecom_webhooks':
                    config['wecom_webhooks'] = self._parse_webhooks(value)
                elif key == 'email_config':
                    config['email_config'] = self._parse_webhooks(value) if value else {}
                elif key == 'auto_alert_levels':
                    config['auto_alert_levels'] = [l.strip() for l in value.split(',') if l.strip()]
                elif key == 'rate_limit_enabled':
                    config['rate_limit_enabled'] = value.lower() in ('true', '1', 'yes')
                elif key == 'rate_limit_window':
                    config['rate_limit_window'] = int(value)
                elif key == 'rate_limit_max_count':
                    config['rate_limit_max_count'] = int(value)
                elif key == 'retry_enabled':
                    config['retry_enabled'] = value.lower() in ('true', '1', 'yes')
                elif key == 'max_retries':
                    config['max_retries'] = int(value)
                elif key == 'retry_delay':
                    config['retry_delay'] = float(value)

            return config
        except Exception as e:
            logger.warning(f"从数据库加载告警配置失败: {e}")
            return {}

    def _parse_webhooks(self, value: str) -> List[Dict]:
        """解析 Webhook 配置 JSON"""
        from src.utils.serialization import from_json
        try:
            if not value:
                return []
            return from_json(value)
        except Exception:
            return []

    async def _apply_config(self, config: Dict[str, Any]):
        """应用告警配置"""
        # 启用异步发送
        alert_manager.configure_async()

        # 配置限流
        alert_manager.configure_rate_limit(
            enabled=config.get('rate_limit_enabled', True),
            window=config.get('rate_limit_window', 60),
            max_count=config.get('rate_limit_max_count', 3)
        )

        # 清除现有渠道
        for channel_name in alert_manager.get_available_channels():
            alert_manager.remove_channel(channel_name)

        # 配置飞书
        feishu_webhooks = config.get('feishu_webhooks', [])
        if feishu_webhooks:
            channel = FeishuAlertChannel(feishu_webhooks)
            channel.configure_retry(
                config.get('retry_enabled', True),
                config.get('max_retries', 3),
                config.get('retry_delay', 1.0)
            )
            alert_manager.add_channel(channel, enabled=True)

        # 配置钉钉
        dingtalk_webhooks = config.get('dingtalk_webhooks', [])
        if dingtalk_webhooks:
            channel = DingtalkAlertChannel(dingtalk_webhooks)
            channel.configure_retry(
                config.get('retry_enabled', True),
                config.get('max_retries', 3),
                config.get('retry_delay', 1.0)
            )
            alert_manager.add_channel(channel, enabled=True)

        # 配置企业微信
        wecom_webhooks = config.get('wecom_webhooks', [])
        if wecom_webhooks:
            channel = WeComAlertChannel(wecom_webhooks)
            channel.configure_retry(
                config.get('retry_enabled', True),
                config.get('max_retries', 3),
                config.get('retry_delay', 1.0)
            )
            alert_manager.add_channel(channel, enabled=True)

        # 配置邮件
        email_config = config.get('email_config', {})
        if email_config and email_config.get('smtp_host'):
            channel = EmailAlertChannel(email_config)
            channel.configure_retry(
                config.get('retry_enabled', True),
                config.get('max_retries', 3),
                config.get('retry_delay', 1.0)
            )
            alert_manager.add_channel(channel, enabled=True)
            email_recipients = len(email_config.get('recipients', []))
        else:
            email_recipients = 0

        logger.info(f"告警配置已应用: 飞书={len(feishu_webhooks)}, 钉钉={len(dingtalk_webhooks)}, 企微={len(wecom_webhooks)}, 邮件={email_recipients}")

    async def reload_config(self):
        """重新加载配置"""
        config = await self._load_config_from_db()
        if config:
            await self._apply_config(config)
            self._config_cache = config
            logger.info("告警配置已重新加载")

    async def send_alert(
        self,
        message: str,
        level: str = "ERROR",
        source: str = "system",
        extra: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        发送告警
        
        Args:
            message: 告警消息
            level: 告警级别 (ERROR, CRITICAL, WARNING, INFO)
            source: 告警来源 (system, task, node, scheduler)
            extra: 额外信息
        
        Returns:
            发送结果
        """
        if not self._initialized:
            await self.initialize()

        # 格式化消息
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        formatted_message = f"{timestamp} | {level: <8} | {source} | {message}"

        if extra:
            extra_str = ' | '.join(f"{k}={v}" for k, v in extra.items())
            formatted_message += f" | {extra_str}"

        # 记录历史
        self._add_history({
            'timestamp': timestamp,
            'level': level,
            'source': source,
            'message': message,
            'extra': extra,
            'status': 'pending'
        })

        # 发送告警
        result = alert_manager.send_alert(formatted_message, level)

        # 更新历史状态
        if self._alert_history:
            self._alert_history[-1]['status'] = result.get('status', 'unknown')

        return result

    async def send_task_alert(
        self,
        task_id: str,
        project_name: str,
        error_message: str,
        exit_code: Optional[int] = None
    ):
        """发送任务失败告警"""
        extra = {
            'task_id': task_id,
            'project': project_name,
        }
        if exit_code is not None:
            extra['exit_code'] = exit_code

        await self.send_alert(
            message=f"任务执行失败: {error_message}",
            level="ERROR",
            source="task",
            extra=extra
        )

    async def send_node_alert(
        self,
        node_name: str,
        node_id: str,
        alert_type: str,  # offline, resource_high, etc.
        message: str
    ):
        """发送节点告警"""
        await self.send_alert(
            message=f"节点 {node_name}: {message}",
            level="CRITICAL" if alert_type == "offline" else "WARNING",
            source="node",
            extra={
                'node_id': node_id,
                'node_name': node_name,
                'alert_type': alert_type
            }
        )

    async def send_test_alert(self, channel: str = "all") -> Dict[str, Any]:
        """发送测试告警"""
        # 确保服务已初始化
        if not self._initialized:
            await self.initialize()

        # 检查是否有启用的渠道
        enabled_channels = alert_manager.get_enabled_channels()
        if not enabled_channels:
            return {
                'success': False,
                'message': '没有配置任何告警渠道，请先添加飞书/钉钉/企业微信 Webhook 或邮件配置',
                'result': {'enabled_channels': []}
            }

        message = f"这是一条测试告警消息 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

        result = alert_manager.send_alert(message, "INFO")

        status = result.get('status', '')

        if status == 'queued':
            return {
                'success': True,
                'message': f'测试告警已发送到 {len(enabled_channels)} 个渠道',
                'result': {'enabled_channels': enabled_channels, **result}
            }
        elif status == 'not_ready':
            return {
                'success': False,
                'message': '告警服务未就绪，请稍后重试',
                'result': result
            }
        elif result.get('rate_limited'):
            return {
                'success': False,
                'message': '发送频率过高，请稍后重试',
                'result': result
            }
        else:
            return {
                'success': False,
                'message': '发送失败，请检查渠道配置',
                'result': result
            }

    def _add_history(self, record: Dict):
        """添加告警历史"""
        self._alert_history.append(record)
        if len(self._alert_history) > self._max_history:
            self._alert_history.pop(0)

    def get_history(
        self,
        limit: int = 50,
        level: Optional[str] = None,
        source: Optional[str] = None
    ) -> List[Dict]:
        """获取告警历史"""
        history = self._alert_history.copy()

        if level:
            history = [h for h in history if h.get('level') == level]
        if source:
            history = [h for h in history if h.get('source') == source]

        # 返回最新的记录
        return list(reversed(history[-limit:]))

    def get_config(self) -> Dict[str, Any]:
        """获取当前配置"""
        return {
            **self._config_cache,
            'enabled_channels': alert_manager.get_enabled_channels(),
            'available_channels': alert_manager.get_available_channels(),
            'rate_limit_stats': alert_manager.get_rate_limit_stats(),
        }

    def get_stats(self) -> Dict[str, Any]:
        """获取告警统计"""
        history = self._alert_history

        # 按级别统计
        level_counts = {}
        for h in history:
            level = h.get('level', 'UNKNOWN')
            level_counts[level] = level_counts.get(level, 0) + 1

        # 按来源统计
        source_counts = {}
        for h in history:
            source = h.get('source', 'unknown')
            source_counts[source] = source_counts.get(source, 0) + 1

        return {
            'total_alerts': len(history),
            'by_level': level_counts,
            'by_source': source_counts,
            'enabled_channels': alert_manager.get_enabled_channels(),
            'rate_limit_stats': alert_manager.get_rate_limit_stats(),
        }


# 全局实例
alert_service = AlertService()
