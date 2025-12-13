# logger/alert_channels/base.py
"""告警渠道基类"""
from abc import ABC, abstractmethod
import asyncio
import os


class AlertChannel(ABC):
    """告警渠道抽象基类"""

    def __init__(self):
        self.retry_enabled = True
        self.max_retries = 3
        self.retry_delay = 1

    def configure_retry(self, enabled, max_retries, retry_delay):
        """配置重试参数"""
        self.retry_enabled = enabled
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    @property
    @abstractmethod
    def channel_name(self):
        """渠道名称"""
        pass


class MultiWebhookChannel(AlertChannel):
    """支持多 Webhook 的告警渠道基类"""

    def __init__(self, webhooks):
        super().__init__()
        self.webhooks = webhooks if webhooks else []

    @abstractmethod
    def _build_payload(self, message, level):
        """构建发送载荷（子类实现）"""
        pass

    @abstractmethod
    def _check_response(self, data):
        """检查响应是否成功（子类实现）"""
        pass

    async def _send_single_alert_with_retry(self, url, payload, webhook_name):
        """发送单条告警（带重试）"""
        if not url:
            return False

        retries = self.max_retries if self.retry_enabled else 1

        for attempt in range(retries):
            try:
                if attempt > 0:
                    await asyncio.sleep(self.retry_delay * (2 ** (attempt - 1)))

                success = await self._send_single_alert(url, payload, webhook_name)
                if success:
                    return True

            except Exception:
                if attempt == retries - 1:
                    return False

        return False

    async def _send_single_alert(self, url, payload, webhook_name):
        """发送单条告警（使用 requests 避免 atexit 问题）"""
        import requests

        # 从环境变量读取是否验证 SSL，默认启用验证
        verify_ssl = os.getenv("ALERT_VERIFY_SSL", "true").lower() != "false"

        try:
            response = requests.post(
                url,
                json=payload,
                headers={'Content-Type': 'application/json;charset=utf-8'},
                timeout=10,
                verify=verify_ssl
            )

            if response.status_code != 200:
                return False

            try:
                response_data = response.json()
            except Exception:
                return False

            success, error_msg = self._check_response(response_data)
            return success

        except requests.Timeout:
            return False
        except requests.RequestException:
            return False
        except Exception:
            return False

    async def send_alert_force(self, message, level):
        """强制发送告警（手动触发，忽略级别过滤）"""
        return await self._do_send_alert(message, level, check_levels=False)

    async def send_alert_with_fallback(self, message, level, default_levels):
        """自动发送告警（优先级：Webhook LEVELS > AUTO_ALERT_LEVELS）"""
        if not self.webhooks:
            return False

        default_levels = default_levels or []
        payload = self._build_payload(message, level)
        tasks = []

        for webhook_config in self.webhooks:
            target_levels = webhook_config.get('levels', [])

            # 优先级逻辑
            should_send = (level in target_levels) if target_levels else (level in default_levels)

            if should_send:
                webhook_url = webhook_config.get('url', '')
                webhook_name = webhook_config.get('name', webhook_url[:50])
                tasks.append(self._send_single_alert_with_retry(webhook_url, payload, webhook_name))

        if not tasks:
            return False

        # 并发发送
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 检查是否至少有一个成功
        return any(isinstance(r, bool) and r for r in results)

    async def _do_send_alert(self, message, level, check_levels=True):
        """内部发送方法"""
        if not self.webhooks:
            return False

        payload = self._build_payload(message, level)
        tasks = []

        for webhook_config in self.webhooks:
            target_levels = webhook_config.get('levels', [])

            should_send = (not target_levels or level in target_levels) if check_levels else True

            if should_send:
                webhook_url = webhook_config.get('url', '')
                webhook_name = webhook_config.get('name', webhook_url[:50])
                tasks.append(self._send_single_alert_with_retry(webhook_url, payload, webhook_name))

        if not tasks:
            return False

        # 并发发送
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 检查是否至少有一个成功
        return any(isinstance(r, bool) and r for r in results)
