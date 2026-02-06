"""告警管理器"""

import asyncio
import time
from collections import defaultdict
from threading import Lock, Thread

from loguru import logger

from antcode_core.common.hash_utils import calculate_content_hash


class RateLimiter:
    def __init__(self, window=60, max_count=3):
        self.window = window
        self.max_count = max_count
        self._records = defaultdict(list)

    def _get_message_key(self, message, level):
        content = f"{level}:{message}"
        return calculate_content_hash(content)

    def should_allow(self, message, level):
        key = self._get_message_key(message, level)
        current_time = time.time()

        self._records[key] = [ts for ts in self._records[key] if current_time - ts < self.window]

        if len(self._records[key]) >= self.max_count:
            remaining = int(self.window - (current_time - self._records[key][0]))
            return False, f"限流 ({remaining}s后可用)"

        self._records[key].append(current_time)
        return True, None

    def clear(self):
        self._records.clear()

    def get_stats(self):
        current_time = time.time()
        active_keys = 0
        total_records = 0

        for _key, timestamps in self._records.items():
            valid_timestamps = [ts for ts in timestamps if current_time - ts < self.window]
            if valid_timestamps:
                active_keys += 1
                total_records += len(valid_timestamps)

        return {"active_keys": active_keys, "total_records": total_records}


class AlertManager:
    def __init__(self):
        self._channels = {}
        self._enabled_channels = []
        self._rate_limiter = None
        self._rate_limit_enabled = False
        self._loop = None
        self._loop_thread = None
        self._async_enabled = False
        self._shutting_down = False
        self._lock = Lock()
        self._pending_tasks = []
        self._has_pending = False

    def configure_async(self):
        """启用异步发送"""
        if self._async_enabled:
            return

        if self._shutting_down:
            self._shutting_down = False

        try:
            self._loop = asyncio.new_event_loop()
            self._loop_thread = Thread(target=self._run_loop, daemon=True)
            self._loop_thread.start()
            self._async_enabled = True
        except Exception as e:
            logger.error(f"启动告警异步事件循环失败: {e}")

    def _run_loop(self):
        """在独立线程中运行事件循环"""
        try:
            asyncio.set_event_loop(self._loop)
            self._loop.run_forever()
        except Exception as e:
            logger.error(f"告警事件循环异常: {e}")

    def configure_rate_limit(self, enabled, window=60, max_count=3):
        """配置限流策略"""
        self._rate_limit_enabled = enabled
        if enabled:
            self._rate_limiter = RateLimiter(window, max_count)
        else:
            self._rate_limiter = None

    def add_channel(self, channel, enabled=True):
        channel_name = channel.channel_name
        self._channels[channel_name] = channel
        if enabled and channel_name not in self._enabled_channels:
            self._enabled_channels.append(channel_name)

    def remove_channel(self, channel_name):
        if channel_name in self._channels:
            del self._channels[channel_name]
            if channel_name in self._enabled_channels:
                self._enabled_channels.remove(channel_name)

    def enable_channel(self, channel_name):
        if channel_name in self._channels and channel_name not in self._enabled_channels:
            self._enabled_channels.append(channel_name)

    def disable_channel(self, channel_name):
        if channel_name in self._enabled_channels:
            self._enabled_channels.remove(channel_name)

    def send_alert(self, message, level="INFO"):
        """发送告警（手动触发）"""
        if self._shutting_down:
            return {"status": "shutting_down"}

        if not self._check_rate_limit(message, level):
            return {"rate_limited": True}

        if not self._enabled_channels:
            return {}

        if not self._async_enabled or not self._loop:
            logger.warning("告警异步发送未就绪")
            return {"status": "not_ready"}

        try:
            future = asyncio.run_coroutine_threadsafe(
                self._send_async(message, level, force=True), self._loop
            )
            self._pending_tasks.append(future)
            self._has_pending = True
            return {"status": "queued"}
        except Exception as e:
            logger.error(f"告警加入队列失败: {e}")
            return {"status": "error"}

    def send_alert_auto(self, message, level, default_levels):
        """发送告警（自动触发）"""
        if self._shutting_down:
            return {"status": "shutting_down"}

        if not self._check_rate_limit(message, level):
            return {"rate_limited": True}

        if not self._enabled_channels:
            return {}

        if not self._async_enabled or not self._loop:
            return {"status": "not_ready"}

        try:
            future = asyncio.run_coroutine_threadsafe(
                self._send_async(message, level, force=False, default_levels=default_levels),
                self._loop,
            )
            self._pending_tasks.append(future)
            self._has_pending = True
            return {"status": "queued"}
        except Exception as e:
            logger.error(f"告警加入队列失败: {e}")
            return {"status": "error"}

    def _check_rate_limit(self, message, level):
        """检查限流"""
        if self._rate_limit_enabled and self._rate_limiter:
            with self._lock:
                allowed, reason = self._rate_limiter.should_allow(message, level)
            if not allowed:
                return False
        return True

    async def _send_async(self, message, level, force=False, default_levels=None):
        """异步发送告警到所有渠道"""
        tasks = []

        for channel_name in self._enabled_channels:
            channel = self._channels.get(channel_name)
            if not channel:
                continue

            try:
                if force:
                    task = channel.send_alert_force(message, level)
                else:
                    task = channel.send_alert_with_fallback(message, level, default_levels)
                tasks.append(task)
            except Exception as e:
                logger.error(f"创建告警发送任务失败 [{channel_name}]: {e}")

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    logger.error(f"告警发送异常: {result}")

    def wait_for_pending_tasks(self, timeout=5):
        """等待所有待完成的任务"""
        if not self._pending_tasks:
            return

        start_time = time.time()
        completed = []

        for idx, future in enumerate(self._pending_tasks):
            if time.time() - start_time >= timeout:
                break

            try:
                time_left = max(0.1, timeout - (time.time() - start_time))
                future.result(timeout=time_left)
                completed.append(idx)
            except Exception:
                completed.append(idx)

        for idx in reversed(completed):
            self._pending_tasks.pop(idx)

        if not self._pending_tasks:
            self._has_pending = False

    def has_pending_alerts(self):
        """检查是否有待完成的告警任务"""
        return self._has_pending and len(self._pending_tasks) > 0

    def shutdown(self, wait=True):
        """关闭事件循环并清理资源"""
        if self._shutting_down:
            return

        self._shutting_down = True

        if self._rate_limiter:
            self._rate_limiter.clear()

        if wait:
            self.wait_for_pending_tasks(timeout=5)

        if not self._loop or not self._loop.is_running():
            return

        try:
            self._loop.call_soon_threadsafe(self._loop.stop)

            if self._loop_thread and self._loop_thread.is_alive():
                self._loop_thread.join(timeout=1)

            self._async_enabled = False
            self._loop = None
            self._loop_thread = None
            self._pending_tasks.clear()

        except Exception as e:
            logger.error(f"关闭告警管理器异常: {e}")

    def get_available_channels(self):
        return list(self._channels.keys())

    def get_enabled_channels(self):
        return self._enabled_channels.copy()

    def get_rate_limit_stats(self):
        if self._rate_limiter:
            return self._rate_limiter.get_stats()
        return {}

    def clear_rate_limit(self):
        if self._rate_limiter:
            self._rate_limiter.clear()

    def __del__(self):
        pass


alert_manager = AlertManager()
