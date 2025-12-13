"""
信号系统 - 事件驱动的组件通信

类似 Scrapy 的信号机制，支持:
- 异步信号处理
- 优先级排序
- 错误隔离
- 弱引用防止内存泄漏
"""
import asyncio
import weakref
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Set
from loguru import logger


class Signal(Enum):
    """预定义信号"""
    # 引擎生命周期
    ENGINE_STARTED = auto()
    ENGINE_STOPPED = auto()
    ENGINE_PAUSED = auto()
    ENGINE_RESUMED = auto()

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


@dataclass
class SignalReceiver:
    """信号接收器"""
    callback: Callable
    priority: int = 0
    weak: bool = False
    _ref: Optional[weakref.ref] = field(default=None, repr=False)

    def __post_init__(self):
        if self.weak and hasattr(self.callback, '__self__'):
            self._ref = weakref.ref(self.callback.__self__)

    @property
    def is_alive(self) -> bool:
        if not self.weak:
            return True
        if self._ref is None:
            return True
        return self._ref() is not None

    async def invoke(self, *args, **kwargs) -> Any:
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
        self._receivers: Dict[Signal, List[SignalReceiver]] = {}
        self._disabled_signals: Set[Signal] = set()
        self._lock = asyncio.Lock()
        self._stats = {
            "signals_sent": 0,
            "handlers_invoked": 0,
            "errors": 0,
        }

    def connect(
        self,
        signal: Signal,
        callback: Callable,
        priority: int = 0,
        weak: bool = False,
    ) -> None:
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

        self._receivers[signal].append(receiver)
        # 按优先级排序（降序）
        self._receivers[signal].sort(key=lambda r: r.priority, reverse=True)

        logger.debug(f"信号连接: {signal.name} <- {callback.__name__}")

    def disconnect(self, signal: Signal, callback: Callable) -> bool:
        """断开信号处理器"""
        if signal not in self._receivers:
            return False

        original_len = len(self._receivers[signal])
        self._receivers[signal] = [
            r for r in self._receivers[signal]
            if r.callback != callback
        ]

        removed = len(self._receivers[signal]) < original_len
        if removed:
            logger.debug(f"信号断开: {signal.name} -x- {callback.__name__}")

        return removed

    def disconnect_all(self, signal: Optional[Signal] = None) -> int:
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
        signal: Signal,
        sender: Any = None,
        **kwargs,
    ) -> List[Any]:
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
        signal: Signal,
        sender: Any = None,
        **kwargs,
    ) -> List[Any]:
        """发送信号，捕获并记录所有异常"""
        try:
            return await self.send(signal, sender, **kwargs)
        except Exception as e:
            logger.error(f"信号发送异常 [{signal.name}]: {e}")
            return []

    def disable(self, signal: Signal) -> None:
        """禁用信号"""
        self._disabled_signals.add(signal)
        logger.debug(f"信号已禁用: {signal.name}")

    def enable(self, signal: Signal) -> None:
        """启用信号"""
        self._disabled_signals.discard(signal)
        logger.debug(f"信号已启用: {signal.name}")

    def is_enabled(self, signal: Signal) -> bool:
        """检查信号是否启用"""
        return signal not in self._disabled_signals

    def get_receivers(self, signal: Signal) -> List[Callable]:
        """获取信号的所有处理器"""
        return [r.callback for r in self._receivers.get(signal, []) if r.is_alive]

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            **self._stats,
            "registered_signals": len(self._receivers),
            "total_receivers": sum(len(r) for r in self._receivers.values()),
            "disabled_signals": len(self._disabled_signals),
        }


# 全局信号管理器
signal_manager = SignalManager()
