"""告警管理器"""

import asyncio
import time
from threading import Lock, Thread

from loguru import logger

from antcode_core.application.services.alert.alert_delivery_status import (
    STATUS_ENQUEUE_FAILED,
    STATUS_NO_CHANNELS,
    STATUS_NOT_READY,
    STATUS_RATE_LIMITED,
    STATUS_SHUTTING_DOWN,
    delivered,
    undelivered,
)
from antcode_core.application.services.alert.alert_rate_limiter import RateLimiter


class AlertManager:
    def __init__(self):
        self._channels = {}
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
            loop = self._loop
            if loop is None:
                raise RuntimeError("alert event loop is not configured")
            asyncio.set_event_loop(loop)
            loop.run_forever()
        except Exception as e:
            logger.error(f"告警事件循环异常: {e}")

    def configure_rate_limit(self, enabled, window=60, max_count=3):
        """配置限流策略"""
        self._rate_limit_enabled = enabled
        if enabled:
            self._rate_limiter = RateLimiter(window, max_count)
        else:
            self._rate_limiter = None

    def replace_channels(self, channels):
        """整体换入一份新拓扑。**必须是单次赋值**。

        重建配置的执行流与发送告警的执行流不是同一条（``configure_async`` 另起
        线程跑本对象的事件循环），此前"逐个 remove 再逐个 add"中间那段空拓扑会
        被发送侧读到：``send_alert_auto`` 判成 no_channels 丢弃告警，
        ``_send_async`` 连日志都不留就 return。整体替换后读到的要么全是旧渠道、
        要么全是新渠道。

        读侧一律先把 ``self._channels`` 取到局部再用：赋值只换绑定、不改动已被
        读走的那个 dict，因此不需要加锁。
        """
        self._channels = {channel.channel_name: channel for channel in channels}

    def send_alert_auto(self, message, level, default_levels, rate_key=None):
        """发送告警（自动触发）。

        每条返回都带 ``status``；未投递的还带结构化 ``error_code``。告警是最不该
        静默失败的东西，任何"没送出去"都必须留下可判定的证据。
        """
        if self._shutting_down:
            return undelivered(STATUS_SHUTTING_DOWN)

        if not self._check_rate_limit(message, level, rate_key):
            return undelivered(STATUS_RATE_LIMITED)

        if not self._channels:
            # 这里曾经 `return {}`：一条真实告警被无声吞掉，调用方与运维都看不到。
            # 不抛异常是因为告警多在异常处理路径上触发，抛出会把主流程一起带走；
            # fail-closed 在这里的含义是"必须可观测"，而不是"打断业务"。
            logger.error(f"告警未投递：没有启用任何告警渠道 | {message}")
            return undelivered(STATUS_NO_CHANNELS)

        if not self._async_enabled or not self._loop:
            logger.error(f"告警未投递：异步发送未就绪 | {message}")
            return undelivered(STATUS_NOT_READY)

        try:
            future = asyncio.run_coroutine_threadsafe(
                self._send_async(message, level, force=False, default_levels=default_levels),
                self._loop,
            )
            self._track_pending(future)
            return delivered()
        except Exception as e:
            logger.error(f"告警加入队列失败: {e} | {message}")
            return undelivered(STATUS_ENQUEUE_FAILED)

    def _track_pending(self, future):
        """记录待完成任务，顺带清理已完成的（否则长期运行无界增长）"""
        with self._lock:
            self._pending_tasks = [f for f in self._pending_tasks if not f.done()]
            self._pending_tasks.append(future)
            self._has_pending = True

    def _check_rate_limit(self, message, level, rate_key=None):
        """检查限流"""
        if self._rate_limit_enabled and self._rate_limiter:
            with self._lock:
                allowed, reason = self._rate_limiter.should_allow(message, level, rate_key)
            if not allowed:
                return False
        return True

    async def _send_async(self, message, level, force=False, default_levels=None):
        """异步发送告警到所有渠道"""
        named_tasks = []

        # 先取到局部：重建随时可能换掉绑定，一轮发送必须跑在同一份拓扑上。
        for channel_name, channel in self._channels.items():
            try:
                if force:
                    task = channel.send_alert_force(message, level)
                else:
                    task = channel.send_alert_for_level(message, level, default_levels)
                named_tasks.append((channel_name, task))
            except Exception as e:
                logger.error(f"创建告警发送任务失败 [{channel_name}]: {e}")

        if not named_tasks:
            return

        results = await asyncio.gather(*(task for _name, task in named_tasks), return_exceptions=True)
        for (channel_name, _task), result in zip(named_tasks, results, strict=True):
            self._log_send_outcome(channel_name, result)

    @staticmethod
    def _log_send_outcome(channel_name, result):
        """自动告警没有调用方接返回值，原因只能落日志——但必须带结构化码落。"""
        if isinstance(result, BaseException):
            logger.error(f"告警发送异常 [{channel_name}]: {result}")
            return
        if not result.ok:
            logger.error(f"告警发送失败 [{channel_name}]: {result.describe()}")

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

    # "已装配"与"已启用"是同一件事：装配侧只装有目标的渠道，单渠道内部的
    # 启用/停用由 WebhookConfig.enabled 在渠道对象里判，manager 这一层的
    # enable/disable 从来没有调用方。两个读法保留是因为 /alert/config 的响应
    # 里两个字段都在用。
    def get_available_channels(self):
        return list(self._channels)

    def get_enabled_channels(self):
        return list(self._channels)

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
