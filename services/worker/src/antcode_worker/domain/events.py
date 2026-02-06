"""
领域事件 - Worker Worker事件定义

本模块定义了领域事件，用于解耦各层之间的通信。
事件驱动架构允许组件之间松耦合地响应状态变化。

合并了原 core/signals.py 的信号系统，提供统一的事件机制。

Requirements: 7.1, 7.2, 7.3, 11.4
"""

import asyncio
import bisect
import contextlib
import inspect
import weakref
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto

from loguru import logger

# ============================================================================
# Signal 枚举 (从 signals.py 合并)
# ============================================================================


class Signal(Enum):
    """预定义信号"""

    # 引擎生命周期
    ENGINE_STARTED = auto()
    ENGINE_STOPPED = auto()
    ENGINE_PAUSED = auto()
    ENGINE_RESUMED = auto()
    ENGINE_DRAINING = auto()  # 优雅关闭中，停止接收新任务

    # 任务生命周期
    TASK_RECEIVED = auto()
    TASK_SCHEDULED = auto()
    TASK_STARTED = auto()
    TASK_COMPLETED = auto()
    TASK_FAILED = auto()
    TASK_CANCELLED = auto()
    TASK_TIMEOUT = auto()
    TASK_RETRYING = auto()

    # 项目同步
    PROJECT_SYNC_STARTED = auto()
    PROJECT_SYNC_COMPLETED = auto()
    PROJECT_SYNC_FAILED = auto()
    PROJECT_CACHED = auto()

    # 执行过程
    EXECUTION_STARTED = auto()
    EXECUTION_COMPLETED = auto()
    EXECUTION_FAILED = auto()
    EXECUTOR_IDLE = auto()
    EXECUTOR_BUSY = auto()
    LOG_RECEIVED = auto()

    # 连接状态
    MASTER_CONNECTED = auto()
    MASTER_DISCONNECTED = auto()
    HEARTBEAT_SENT = auto()
    HEARTBEAT_FAILED = auto()

    # 资源监控
    RESOURCE_WARNING = auto()
    RESOURCE_CRITICAL = auto()

    # 中间件
    MIDDLEWARE_ENABLED = auto()
    MIDDLEWARE_DISABLED = auto()


# ============================================================================
# SignalManager (从 signals.py 合并)
# ============================================================================


@dataclass
class SignalReceiver:
    """信号接收器"""

    callback: object
    priority: int = 0
    weak: bool = False
    _ref: weakref.ref = field(default=None, repr=False)

    def __post_init__(self):
        if self.weak and hasattr(self.callback, "__self__"):
            self._ref = weakref.ref(self.callback.__self__)

    @property
    def is_alive(self):
        if not self.weak:
            return True
        if self._ref is None:
            return True
        return self._ref() is not None

    async def invoke(self, *args, **kwargs):
        if not self.is_alive:
            return None

        try:
            result = self.callback(*args, **kwargs)
            if asyncio.iscoroutine(result):
                return await result
            return result
        except Exception as e:
            logger.error(f"信号处理异常: {e}")
            raise


class SignalManager:
    """
    信号管理器

    特性:
    - 异步信号发送
    - 优先级排序
    - 错误隔离（单个处理器异常不影响其他）
    - 弱引用支持
    - 信号过滤
    """

    def __init__(self):
        self._receivers = {}
        self._disabled_signals = set()
        self._lock = asyncio.Lock()
        self._stats = {
            "signals_sent": 0,
            "handlers_invoked": 0,
            "errors": 0,
        }

    def connect(
        self,
        signal,
        callback,
        priority=0,
        weak=False,
    ):
        """
        连接信号处理器

        Args:
            signal: 信号类型
            callback: 回调函数（支持同步/异步）
            priority: 优先级（数字越大越先执行）
            weak: 是否使用弱引用
        """
        if signal not in self._receivers:
            self._receivers[signal] = []

        receiver = SignalReceiver(
            callback=callback,
            priority=priority,
            weak=weak,
        )
        receivers = self._receivers[signal]
        priorities = [-r.priority for r in receivers]
        insert_at = bisect.bisect_left(priorities, -receiver.priority)
        receivers.insert(insert_at, receiver)

        logger.debug(f"信号连接: {signal.name} <- {callback.__name__}")

    def disconnect(self, signal, callback):
        """断开信号处理器"""
        if signal not in self._receivers:
            return False

        original_len = len(self._receivers[signal])
        self._receivers[signal] = [r for r in self._receivers[signal] if r.callback != callback]

        removed = len(self._receivers[signal]) < original_len
        if removed:
            logger.debug(f"信号断开: {signal.name} -x- {callback.__name__}")

        return removed

    def disconnect_all(self, signal=None):
        """断开所有处理器"""
        if signal:
            count = len(self._receivers.get(signal, []))
            self._receivers[signal] = []
            return count

        count = sum(len(receivers) for receivers in self._receivers.values())
        self._receivers.clear()
        return count

    async def send(
        self,
        signal,
        sender=None,
        **kwargs,
    ):
        """
        发送信号

        Args:
            signal: 信号类型
            sender: 发送者
            **kwargs: 传递给处理器的参数

        Returns:
            所有处理器的返回值列表
        """
        if signal in self._disabled_signals:
            return []

        self._stats["signals_sent"] += 1

        receivers = self._receivers.get(signal, [])
        if not receivers:
            return []

        # 清理失效的弱引用
        receivers = [r for r in receivers if r.is_alive]
        self._receivers[signal] = receivers

        results = []
        for receiver in receivers:
            try:
                self._stats["handlers_invoked"] += 1
                result = await receiver.invoke(signal=signal, sender=sender, **kwargs)
                results.append(result)
            except Exception as e:
                self._stats["errors"] += 1
                logger.error(f"信号 {signal.name} 处理异常: {e}")
                # 继续执行其他处理器

        return results

    async def send_catch_log(
        self,
        signal,
        sender=None,
        **kwargs,
    ):
        """发送信号，捕获并记录所有异常"""
        try:
            return await self.send(signal, sender, **kwargs)
        except Exception as e:
            logger.error(f"信号发送异常 [{signal.name}]: {e}")
            return []

    def disable(self, signal):
        """禁用信号"""
        self._disabled_signals.add(signal)
        logger.debug(f"信号已禁用: {signal.name}")

    def enable(self, signal):
        """启用信号"""
        self._disabled_signals.discard(signal)
        logger.debug(f"信号已启用: {signal.name}")

    def is_enabled(self, signal):
        """检查信号是否启用"""
        return signal not in self._disabled_signals

    def get_receivers(self, signal):
        """获取信号的所有处理器"""
        return [r.callback for r in self._receivers.get(signal, []) if r.is_alive]

    def get_stats(self):
        """获取统计信息"""
        return {
            **self._stats,
            "registered_signals": len(self._receivers),
            "total_receivers": sum(len(r) for r in self._receivers.values()),
            "disabled_signals": len(self._disabled_signals),
        }


# 全局信号管理器
signal_manager = SignalManager()


# ============================================================================
# 领域事件 (原 events.py 内容)
# ============================================================================


@dataclass
class DomainEvent:
    """领域事件基类"""

    timestamp: datetime = field(default_factory=datetime.now)
    event_id: str = field(default_factory=lambda: "")

    def __post_init__(self):
        if not self.event_id:
            import uuid

            self.event_id = str(uuid.uuid4())


@dataclass
class ConnectionStateChanged(DomainEvent):
    """连接状态变更事件"""

    old_state: str = "disconnected"
    new_state: str = "disconnected"
    reason: str | None = None


@dataclass
class HeartbeatSent(DomainEvent):
    """心跳发送成功事件"""

    worker_id: str = ""
    latency_ms: float = 0.0


@dataclass
class HeartbeatFailed(DomainEvent):
    """心跳发送失败事件"""

    worker_id: str = ""
    error: str = ""
    consecutive_failures: int = 0


@dataclass
class LogChunkSent(DomainEvent):
    """日志分片发送事件（新双通道架构）"""

    execution_id: str = ""
    log_type: str = ""  # stdout/stderr
    offset: int = 0
    size: int = 0
    is_final: bool = False


@dataclass
class LogChunkAcked(DomainEvent):
    """日志分片确认事件（新双通道架构）"""

    execution_id: str = ""
    log_type: str = ""  # stdout/stderr
    ack_offset: int = 0
    ok: bool = True
    error: str = ""


@dataclass
class LogRealtimePushed(DomainEvent):
    """实时日志推送事件（新双通道架构）"""

    execution_id: str = ""
    log_type: str = ""  # stdout/stderr
    content_length: int = 0


@dataclass
class LogTransferCompleted(DomainEvent):
    """日志传输完成事件（新双通道架构）"""

    execution_id: str = ""
    log_type: str = ""  # stdout/stderr
    total_size: int = 0


@dataclass
class TaskReceived(DomainEvent):
    """任务接收事件"""

    task_id: str = ""
    task_type: str = ""
    accepted: bool = True
    reject_reason: str | None = None


@dataclass
class TaskStatusChanged(DomainEvent):
    """任务状态变更事件"""

    execution_id: str = ""
    old_status: str = ""
    new_status: str = ""
    exit_code: int | None = None
    error_message: str | None = None


@dataclass
class TaskCancelled(DomainEvent):
    """任务取消事件"""

    execution_id: str = ""
    success: bool = False
    reason: str | None = None


@dataclass
class MessageDropped(DomainEvent):
    """消息丢弃事件（背压）"""

    message_type: str = ""
    reason: str = ""
    dropped_count: int = 1


@dataclass
class ReconnectionAttempt(DomainEvent):
    """重连尝试事件"""

    attempt_number: int = 0
    delay_seconds: float = 0.0


@dataclass
class ReconnectionSuccess(DomainEvent):
    """重连成功事件"""

    attempt_number: int = 0
    pending_messages_count: int = 0


@dataclass
class ReconnectionFailed(DomainEvent):
    """重连失败事件"""

    attempt_number: int = 0
    error: str = ""
    next_delay_seconds: float = 0.0


@dataclass
class CircuitBreakerStateChanged(DomainEvent):
    """
    断路器状态变更事件

    当断路器状态发生变化时发布此事件。

    Requirements: 18.5
    """

    circuit_name: str = ""
    old_state: str = ""  # CircuitState 的值
    new_state: str = ""  # CircuitState 的值
    failure_count: int = 0
    reason: str | None = None


# ============================================================================
# EventBus (统一事件总线)
# ============================================================================


class EventBus:
    """
    统一事件总线实现

    用于在组件之间发布和订阅领域事件。
    支持异步回调。

    Requirements: 7.1, 7.2, 7.3
    """

    def __init__(self):
        self._handlers: dict[type, list] = {}

    def subscribe(self, event_type: type, handler):
        """
        订阅事件

        Args:
            event_type: 事件类型
            handler: 事件处理函数（同步或异步）
        """
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: type, handler):
        """
        取消订阅

        Args:
            event_type: 事件类型
            handler: 事件处理函数
        """
        if event_type in self._handlers:
            with contextlib.suppress(ValueError):
                self._handlers[event_type].remove(handler)

    async def publish(self, event: DomainEvent):
        """
        发布事件

        Args:
            event: 领域事件
        """
        event_type = type(event)
        handlers = self._handlers.get(event_type, [])

        for handler in handlers:
            try:
                if inspect.iscoroutinefunction(handler):
                    await handler(event)
                else:
                    handler(event)
            except Exception as e:
                # 记录错误但不中断其他处理器
                logger.error(f"Event handler error for {event_type.__name__}: {e}")

    def publish_sync(self, event: DomainEvent):
        """
        同步发布事件（仅调用同步处理器）

        Args:
            event: 领域事件
        """
        event_type = type(event)
        handlers = self._handlers.get(event_type, [])

        for handler in handlers:
            try:
                if not inspect.iscoroutinefunction(handler):
                    handler(event)
            except Exception as e:
                logger.error(f"Event handler error for {event_type.__name__}: {e}")


# 全局事件总线实例
event_bus = EventBus()
