"""
Gateway 重连管理模块

实现指数退避重连和 receipt idempotency on retry。

Requirements: 5.6, 5.7
"""

from __future__ import annotations

import asyncio
import contextlib
import random
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from loguru import logger


class ReconnectState(str, Enum):
    """重连状态"""

    IDLE = "idle"                    # 空闲（已连接）
    DISCONNECTED = "disconnected"    # 已断开
    RECONNECTING = "reconnecting"    # 重连中
    BACKOFF = "backoff"              # 退避等待中
    FAILED = "failed"                # 重连失败（达到最大次数）
    STOPPED = "stopped"              # 已停止


@dataclass
class ReconnectConfig:
    """重连配置"""

    # 退避配置
    initial_backoff: float = 1.0           # 初始退避时间（秒）
    max_backoff: float = 60.0              # 最大退避时间（秒）
    backoff_multiplier: float = 2.0        # 退避乘数
    jitter_factor: float = 0.1             # 抖动因子（0-1）

    # 重试配置
    max_attempts: int = 0                  # 最大重试次数（0 = 无限）
    reset_backoff_on_success: bool = True  # 成功后重置退避

    # 健康检查配置
    health_check_interval: float = 30.0    # 健康检查间隔（秒）
    health_check_timeout: float = 5.0      # 健康检查超时（秒）

    # 幂等性配置
    enable_receipt_tracking: bool = True   # 启用 receipt 跟踪
    receipt_cache_size: int = 10000        # receipt 缓存大小
    receipt_ttl: float = 300.0             # receipt TTL（秒）


@dataclass
class ReconnectStats:
    """重连统计"""

    total_reconnects: int = 0
    successful_reconnects: int = 0
    failed_reconnects: int = 0
    current_attempt: int = 0
    current_backoff: float = 0.0
    last_reconnect_time: datetime | None = None
    last_success_time: datetime | None = None
    last_failure_time: datetime | None = None
    last_failure_reason: str | None = None
    total_downtime_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "total_reconnects": self.total_reconnects,
            "successful_reconnects": self.successful_reconnects,
            "failed_reconnects": self.failed_reconnects,
            "current_attempt": self.current_attempt,
            "current_backoff": self.current_backoff,
            "last_reconnect_time": (
                self.last_reconnect_time.isoformat()
                if self.last_reconnect_time
                else None
            ),
            "last_success_time": (
                self.last_success_time.isoformat()
                if self.last_success_time
                else None
            ),
            "last_failure_time": (
                self.last_failure_time.isoformat()
                if self.last_failure_time
                else None
            ),
            "last_failure_reason": self.last_failure_reason,
            "total_downtime_seconds": self.total_downtime_seconds,
        }


class ReconnectManager:
    """
    重连管理器

    实现指数退避重连策略，支持：
    - 可配置的退避参数
    - 抖动（jitter）防止惊群效应
    - 最大重试次数限制
    - 健康检查
    - Receipt 幂等性跟踪

    Requirements: 5.6, 5.7
    """

    def __init__(
        self,
        config: ReconnectConfig | None = None,
        connect_func: Callable[[], Coroutine[Any, Any, bool]] | None = None,
        health_check_func: Callable[[], Coroutine[Any, Any, bool]] | None = None,
    ):
        self._config = config or ReconnectConfig()
        self._connect_func = connect_func
        self._health_check_func = health_check_func

        # 状态
        self._state = ReconnectState.IDLE
        self._stats = ReconnectStats()
        self._current_backoff = self._config.initial_backoff

        # 任务
        self._reconnect_task: asyncio.Task | None = None
        self._health_check_task: asyncio.Task | None = None

        # 事件
        self._stop_event = asyncio.Event()
        self._connected_event = asyncio.Event()
        self._connected_event.set()  # 初始假设已连接

        # Receipt 跟踪
        self._pending_receipts: dict[str, _ReceiptEntry] = {}
        self._completed_receipts: dict[str, _ReceiptEntry] = {}

        # 回调
        self._on_reconnect_start: Callable[[], None] | None = None
        self._on_reconnect_success: Callable[[], None] | None = None
        self._on_reconnect_failure: Callable[[str], None] | None = None

    @property
    def state(self) -> ReconnectState:
        """获取当前状态"""
        return self._state

    @property
    def is_connected(self) -> bool:
        """是否已连接"""
        return self._state == ReconnectState.IDLE

    def get_stats(self) -> ReconnectStats:
        """获取统计信息"""
        return self._stats

    def set_connect_func(
        self, func: Callable[[], Coroutine[Any, Any, bool]]
    ) -> None:
        """设置连接函数"""
        self._connect_func = func

    def set_health_check_func(
        self, func: Callable[[], Coroutine[Any, Any, bool]]
    ) -> None:
        """设置健康检查函数"""
        self._health_check_func = func

    def on_reconnect_start(self, callback: Callable[[], None]) -> None:
        """注册重连开始回调"""
        self._on_reconnect_start = callback

    def on_reconnect_success(self, callback: Callable[[], None]) -> None:
        """注册重连成功回调"""
        self._on_reconnect_success = callback

    def on_reconnect_failure(self, callback: Callable[[str], None]) -> None:
        """注册重连失败回调"""
        self._on_reconnect_failure = callback

    async def start(self) -> None:
        """启动重连管理器"""
        self._stop_event.clear()

        # 启动健康检查任务
        if self._health_check_func:
            self._health_check_task = asyncio.create_task(
                self._health_check_loop()
            )

    async def stop(self) -> None:
        """停止重连管理器"""
        self._stop_event.set()
        self._state = ReconnectState.STOPPED

        # 取消任务
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reconnect_task

        if self._health_check_task and not self._health_check_task.done():
            self._health_check_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._health_check_task

    def notify_disconnected(self, reason: str = "") -> None:
        """
        通知断开连接

        触发重连流程。
        """
        if self._state in (ReconnectState.STOPPED, ReconnectState.RECONNECTING):
            return

        self._state = ReconnectState.DISCONNECTED
        self._connected_event.clear()
        self._stats.last_failure_time = datetime.now()
        self._stats.last_failure_reason = reason

        logger.warning(f"连接断开: {reason}")

        # 启动重连任务
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    def notify_connected(self) -> None:
        """
        通知已连接

        重置退避并更新状态。
        """
        self._state = ReconnectState.IDLE
        self._connected_event.set()
        self._stats.last_success_time = datetime.now()

        if self._config.reset_backoff_on_success:
            self._current_backoff = self._config.initial_backoff
            self._stats.current_attempt = 0

    async def reconnect(self) -> bool:
        """
        执行重连

        Returns:
            是否重连成功
        """
        if not self._connect_func:
            logger.error("未设置连接函数")
            return False

        self._state = ReconnectState.RECONNECTING
        self._stats.total_reconnects += 1
        self._stats.last_reconnect_time = datetime.now()

        # 触发回调
        if self._on_reconnect_start:
            try:
                self._on_reconnect_start()
            except Exception as e:
                logger.error(f"重连开始回调异常: {e}")

        try:
            success = await self._connect_func()

            if success:
                self._stats.successful_reconnects += 1
                self.notify_connected()

                # 触发回调
                if self._on_reconnect_success:
                    try:
                        self._on_reconnect_success()
                    except Exception as e:
                        logger.error(f"重连成功回调异常: {e}")

                logger.info("重连成功")
                return True
            else:
                self._stats.failed_reconnects += 1
                self._state = ReconnectState.DISCONNECTED
                return False

        except Exception as e:
            self._stats.failed_reconnects += 1
            self._stats.last_failure_reason = str(e)
            self._state = ReconnectState.DISCONNECTED

            # 触发回调
            if self._on_reconnect_failure:
                try:
                    self._on_reconnect_failure(str(e))
                except Exception as cb_error:
                    logger.error(f"重连失败回调异常: {cb_error}")

            logger.error(f"重连失败: {e}")
            return False

    async def wait_connected(self, timeout: float | None = None) -> bool:
        """
        等待连接

        Args:
            timeout: 超时时间（秒）

        Returns:
            是否已连接
        """
        try:
            await asyncio.wait_for(
                self._connected_event.wait(),
                timeout=timeout,
            )
            return True
        except TimeoutError:
            return False

    # ==================== Receipt 幂等性 ====================

    def track_receipt(self, receipt_id: str, operation: str) -> bool:
        """
        跟踪 receipt

        Args:
            receipt_id: Receipt ID
            operation: 操作类型

        Returns:
            是否为新 receipt（True = 新，False = 重复）
        """
        if not self._config.enable_receipt_tracking:
            return True

        # 检查是否已完成
        if receipt_id in self._completed_receipts:
            entry = self._completed_receipts[receipt_id]
            if not entry.is_expired(self._config.receipt_ttl):
                logger.debug(f"Receipt 已完成，跳过: {receipt_id}")
                return False

        # 检查是否正在处理
        if receipt_id in self._pending_receipts:
            logger.debug(f"Receipt 正在处理: {receipt_id}")
            return False

        # 添加到 pending
        self._pending_receipts[receipt_id] = _ReceiptEntry(
            receipt_id=receipt_id,
            operation=operation,
            created_at=time.time(),
        )

        # 清理过期条目
        self._cleanup_receipts()

        return True

    def complete_receipt(self, receipt_id: str, success: bool) -> None:
        """
        完成 receipt

        Args:
            receipt_id: Receipt ID
            success: 是否成功
        """
        if not self._config.enable_receipt_tracking:
            return

        if receipt_id in self._pending_receipts:
            entry = self._pending_receipts.pop(receipt_id)
            entry.completed_at = time.time()
            entry.success = success
            self._completed_receipts[receipt_id] = entry

    def is_receipt_completed(self, receipt_id: str) -> bool | None:
        """
        检查 receipt 是否已完成

        Args:
            receipt_id: Receipt ID

        Returns:
            True = 成功完成，False = 失败完成，None = 未完成或不存在
        """
        if receipt_id in self._completed_receipts:
            entry = self._completed_receipts[receipt_id]
            if not entry.is_expired(self._config.receipt_ttl):
                return entry.success
        return None

    def get_pending_receipts(self) -> list[str]:
        """获取待处理的 receipt 列表"""
        return list(self._pending_receipts.keys())

    # ==================== 私有方法 ====================

    async def _reconnect_loop(self) -> None:
        """重连循环"""
        disconnect_time = time.time()

        while not self._stop_event.is_set():
            # 检查最大重试次数
            if (
                self._config.max_attempts > 0
                and self._stats.current_attempt >= self._config.max_attempts
            ):
                self._state = ReconnectState.FAILED
                self._stats.total_downtime_seconds += time.time() - disconnect_time

                logger.error(
                    f"达到最大重试次数 ({self._config.max_attempts})，停止重连"
                )
                return

            # 计算退避时间
            backoff = self._calculate_backoff()
            self._stats.current_backoff = backoff
            self._stats.current_attempt += 1

            logger.info(
                f"等待 {backoff:.2f} 秒后重连 "
                f"(尝试 {self._stats.current_attempt})"
            )

            # 等待退避时间
            self._state = ReconnectState.BACKOFF
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=backoff,
                )
                # 如果 stop_event 被设置，退出循环
                return
            except TimeoutError:
                pass

            # 尝试重连
            success = await self.reconnect()

            if success:
                self._stats.total_downtime_seconds += time.time() - disconnect_time
                return

            # 增加退避时间
            self._current_backoff = min(
                self._current_backoff * self._config.backoff_multiplier,
                self._config.max_backoff,
            )

    async def _health_check_loop(self) -> None:
        """健康检查循环"""
        while not self._stop_event.is_set():
            try:
                await asyncio.sleep(self._config.health_check_interval)

                if self._state != ReconnectState.IDLE:
                    continue

                if not self._health_check_func:
                    continue

                # 执行健康检查
                try:
                    healthy = await asyncio.wait_for(
                        self._health_check_func(),
                        timeout=self._config.health_check_timeout,
                    )

                    if not healthy:
                        self.notify_disconnected("健康检查失败")

                except TimeoutError:
                    self.notify_disconnected("健康检查超时")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"健康检查异常: {e}")

    def _calculate_backoff(self) -> float:
        """计算退避时间（带抖动）"""
        # 基础退避时间
        backoff = self._current_backoff

        # 添加抖动
        if self._config.jitter_factor > 0:
            jitter = backoff * self._config.jitter_factor
            backoff += random.uniform(-jitter, jitter)

        # 确保在范围内
        return max(
            self._config.initial_backoff,
            min(backoff, self._config.max_backoff),
        )

    def _cleanup_receipts(self) -> None:
        """清理过期的 receipt"""
        ttl = self._config.receipt_ttl

        # 清理已完成的
        expired = [
            rid for rid, entry in self._completed_receipts.items()
            if entry.is_expired(ttl)
        ]
        for rid in expired:
            del self._completed_receipts[rid]

        # 限制缓存大小
        if len(self._completed_receipts) > self._config.receipt_cache_size:
            # 按时间排序，删除最旧的
            sorted_entries = sorted(
                self._completed_receipts.items(),
                key=lambda x: x[1].completed_at or 0,
            )
            to_remove = len(self._completed_receipts) - self._config.receipt_cache_size
            for rid, _ in sorted_entries[:to_remove]:
                del self._completed_receipts[rid]


@dataclass
class _ReceiptEntry:
    """Receipt 条目"""

    receipt_id: str
    operation: str
    created_at: float
    completed_at: float | None = None
    success: bool | None = None

    def is_expired(self, ttl: float) -> bool:
        """检查是否过期"""
        reference_time = self.completed_at or self.created_at
        return time.time() - reference_time > ttl


class ExponentialBackoff:
    """
    指数退避计算器

    独立的退避计算工具类。
    """

    def __init__(
        self,
        initial: float = 1.0,
        maximum: float = 60.0,
        multiplier: float = 2.0,
        jitter: float = 0.1,
    ):
        self._initial = initial
        self._maximum = maximum
        self._multiplier = multiplier
        self._jitter = jitter
        self._current = initial
        self._attempt = 0

    def next_backoff(self) -> float:
        """获取下一个退避时间"""
        backoff = self._current

        # 添加抖动
        if self._jitter > 0:
            jitter_amount = backoff * self._jitter
            backoff += random.uniform(-jitter_amount, jitter_amount)

        # 更新状态
        self._current = min(self._current * self._multiplier, self._maximum)
        self._attempt += 1

        return max(self._initial, min(backoff, self._maximum))

    def reset(self) -> None:
        """重置退避"""
        self._current = self._initial
        self._attempt = 0

    @property
    def attempt(self) -> int:
        """当前尝试次数"""
        return self._attempt

    @property
    def current(self) -> float:
        """当前退避时间"""
        return self._current
