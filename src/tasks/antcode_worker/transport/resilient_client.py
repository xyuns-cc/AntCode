"""
弹性连接客户端

提供连接弹性功能，包括：
- 指数退避重连
- 断开期间消息缓冲
- 重连后消息重发

Requirements: 7.1, 7.2, 7.3, 7.4, 7.5
"""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Callable, Awaitable, List, Deque, Union
from collections import deque
from enum import Enum

from loguru import logger

from ..domain.models import (
    ConnectionConfig,
    ConnectionState,
    Heartbeat,
    LogEntry,
    TaskStatus,
    TaskDispatch,
    TaskCancel,
    GrpcMetrics,
    Protocol,
)
from ..domain.events import (
    event_bus,
    ConnectionStateChanged,
    ReconnectionAttempt,
    ReconnectionSuccess,
    ReconnectionFailed,
)
from .protocol import TransportProtocol, ConnectionError, SendError


class MessageType(str, Enum):
    """缓冲消息类型"""
    HEARTBEAT = "heartbeat"
    LOG_BATCH = "log_batch"
    TASK_STATUS = "task_status"


@dataclass
class BufferedMessage:
    """缓冲的消息"""
    message_type: MessageType
    payload: Union[Heartbeat, List[LogEntry], TaskStatus]
    timestamp: datetime = field(default_factory=datetime.now)
    retry_count: int = 0


class ExponentialBackoff:
    """
    指数退避计算器
    
    实现指数退避算法，用于重连延迟计算。
    
    Requirements: 7.1, 7.2
    """
    
    def __init__(
        self,
        base_delay: float = 5.0,
        max_delay: float = 60.0,
        multiplier: float = 2.0,
        jitter: float = 0.1,
    ):
        """
        初始化指数退避计算器
        
        Args:
            base_delay: 初始延迟（秒），默认 5 秒
            max_delay: 最大延迟（秒），默认 60 秒
            multiplier: 延迟倍数，默认 2.0
            jitter: 抖动因子，默认 0.1（10%）
        """
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._multiplier = multiplier
        self._jitter = jitter
        self._attempt = 0
    
    @property
    def base_delay(self) -> float:
        return self._base_delay
    
    @property
    def max_delay(self) -> float:
        return self._max_delay
    
    @property
    def multiplier(self) -> float:
        return self._multiplier
    
    @property
    def attempt(self) -> int:
        return self._attempt
    
    def next_delay(self) -> float:
        """
        计算下一次重试的延迟时间
        
        Returns:
            延迟时间（秒）
        """
        # 计算基础延迟：base_delay * (multiplier ^ attempt)
        delay = self._base_delay * (self._multiplier ** self._attempt)
        
        # 限制最大延迟
        delay = min(delay, self._max_delay)
        
        # 添加抖动（可选）
        if self._jitter > 0:
            import random
            jitter_range = delay * self._jitter
            delay += random.uniform(-jitter_range, jitter_range)
            delay = max(0, delay)  # 确保非负
        
        self._attempt += 1
        return delay
    
    def reset(self):
        """重置重试计数"""
        self._attempt = 0
    
    def get_delay_for_attempt(self, attempt: int) -> float:
        """
        获取指定尝试次数的延迟时间（不改变内部状态）
        
        Args:
            attempt: 尝试次数（从 0 开始）
            
        Returns:
            延迟时间（秒）
        """
        delay = self._base_delay * (self._multiplier ** attempt)
        return min(delay, self._max_delay)


class MessageBuffer:
    """
    消息缓冲区
    
    在断开连接期间缓冲消息，重连后重发。
    
    Requirements: 7.3, 7.4
    """
    
    def __init__(self, max_size: int = 1000):
        """
        初始化消息缓冲区
        
        Args:
            max_size: 最大缓冲消息数
        """
        self._max_size = max_size
        self._buffer: Deque[BufferedMessage] = deque(maxlen=max_size)
        self._lock = asyncio.Lock()
        self._dropped_count = 0
    
    @property
    def size(self) -> int:
        return len(self._buffer)
    
    @property
    def dropped_count(self) -> int:
        return self._dropped_count
    
    async def add(self, message: BufferedMessage) -> bool:
        """
        添加消息到缓冲区
        
        Args:
            message: 要缓冲的消息
            
        Returns:
            是否成功添加（缓冲区满时会丢弃最旧消息）
        """
        async with self._lock:
            if len(self._buffer) >= self._max_size:
                # 丢弃最旧的消息
                self._buffer.popleft()
                self._dropped_count += 1
                logger.warning(f"消息缓冲区已满，丢弃最旧消息")
            
            self._buffer.append(message)
            return True
    
    async def get_all(self) -> List[BufferedMessage]:
        """
        获取并清空所有缓冲消息
        
        Returns:
            所有缓冲的消息列表
        """
        async with self._lock:
            messages = list(self._buffer)
            self._buffer.clear()
            return messages
    
    async def clear(self):
        """清空缓冲区"""
        async with self._lock:
            self._buffer.clear()



class ResilientGrpcClient(TransportProtocol):
    """
    弹性 gRPC 客户端
    
    包装 GrpcClient，提供连接弹性功能：
    - 自动重连（指数退避）
    - 断开期间消息缓冲
    - 重连后消息重发
    - 任务继续执行
    
    Requirements: 7.1, 7.2, 7.3, 7.4, 7.5
    """
    
    def __init__(
        self,
        transport: TransportProtocol,
        base_delay: float = 5.0,
        max_delay: float = 60.0,
        buffer_size: int = 1000,
    ):
        """
        初始化弹性客户端
        
        Args:
            transport: 底层传输协议（通常是 GrpcClient）
            base_delay: 重连初始延迟（秒）
            max_delay: 重连最大延迟（秒）
            buffer_size: 消息缓冲区大小
        """
        self._transport = transport
        self._backoff = ExponentialBackoff(
            base_delay=base_delay,
            max_delay=max_delay,
        )
        self._message_buffer = MessageBuffer(max_size=buffer_size)
        
        self._config: Optional[ConnectionConfig] = None
        self._state = ConnectionState.DISCONNECTED
        self._running = False
        self._reconnect_task: Optional[asyncio.Task] = None
        
        # 回调函数
        self._on_task_dispatch: Optional[Callable[[TaskDispatch], Awaitable[None]]] = None
        self._on_task_cancel: Optional[Callable[[TaskCancel], Awaitable[None]]] = None
        
        # 注册底层传输的回调
        self._transport.on_task_dispatch(self._handle_task_dispatch)
        self._transport.on_task_cancel(self._handle_task_cancel)
    
    @property
    def protocol_name(self) -> str:
        return f"resilient-{self._transport.protocol_name}"
    
    @property
    def is_connected(self) -> bool:
        return self._transport.is_connected
    
    @property
    def metrics(self) -> GrpcMetrics:
        return self._transport.metrics
    
    @property
    def state(self) -> ConnectionState:
        return self._state
    
    @property
    def buffered_message_count(self) -> int:
        return self._message_buffer.size
    
    async def connect(self, config: ConnectionConfig) -> bool:
        """
        建立连接
        
        Requirements: 1.2
        """
        self._config = config
        self._running = True
        
        # 使用配置中的重连参数
        self._backoff = ExponentialBackoff(
            base_delay=config.reconnect_base_delay,
            max_delay=config.reconnect_max_delay,
        )
        
        try:
            await self._set_state(ConnectionState.CONNECTING)
            success = await self._transport.connect(config)
            
            if success:
                await self._set_state(ConnectionState.CONNECTED)
                self._backoff.reset()
                return True
            else:
                await self._set_state(ConnectionState.DISCONNECTED)
                # 启动重连
                self._start_reconnect_loop()
                return False
                
        except Exception as e:
            logger.error(f"连接失败: {e}")
            await self._set_state(ConnectionState.DISCONNECTED)
            self._start_reconnect_loop()
            return False
    
    async def disconnect(self):
        """断开连接"""
        self._running = False
        
        # 停止重连任务
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
            self._reconnect_task = None
        
        # 断开底层连接
        await self._transport.disconnect()
        await self._set_state(ConnectionState.DISCONNECTED)
    
    async def send_heartbeat(self, heartbeat: Heartbeat) -> bool:
        """
        发送心跳消息
        
        如果断开连接，不缓冲心跳（心跳是实时状态）
        
        Requirements: 2.2, 2.3
        """
        if self.is_connected:
            return await self._transport.send_heartbeat(heartbeat)
        else:
            # 断开时不缓冲心跳，因为心跳是实时状态
            logger.debug("连接断开，跳过心跳发送")
            return False
    
    async def send_logs(self, logs: List[LogEntry]) -> bool:
        """
        发送日志批次
        
        如果断开连接，缓冲日志等待重连后发送
        
        Requirements: 3.1, 3.2, 3.3, 3.4, 7.4
        """
        if self.is_connected:
            success = await self._transport.send_logs(logs)
            if not success:
                # 发送失败，缓冲消息
                await self._buffer_message(MessageType.LOG_BATCH, logs)
            return success
        else:
            # 断开时缓冲日志
            await self._buffer_message(MessageType.LOG_BATCH, logs)
            logger.debug(f"连接断开，缓冲 {len(logs)} 条日志")
            return False
    
    async def send_task_status(self, status: TaskStatus) -> bool:
        """
        发送任务状态更新
        
        如果断开连接，缓冲状态更新等待重连后发送
        
        Requirements: 4.1, 4.2, 7.4
        """
        if self.is_connected:
            success = await self._transport.send_task_status(status)
            if not success:
                # 发送失败，缓冲消息
                await self._buffer_message(MessageType.TASK_STATUS, status)
            return success
        else:
            # 断开时缓冲状态更新
            await self._buffer_message(MessageType.TASK_STATUS, status)
            logger.debug(f"连接断开，缓冲任务状态: {status.execution_id} -> {status.status}")
            return False
    
    async def send_task_ack(self, task_id: str, accepted: bool, reason: Optional[str] = None) -> bool:
        """发送任务确认"""
        if self.is_connected:
            return await self._transport.send_task_ack(task_id, accepted, reason)
        return False
    
    async def send_cancel_ack(self, task_id: str, success: bool, reason: Optional[str] = None) -> bool:
        """发送取消确认"""
        if self.is_connected:
            return await self._transport.send_cancel_ack(task_id, success, reason)
        return False
    
    def on_task_dispatch(self, callback: Callable[[TaskDispatch], Awaitable[None]]):
        """注册任务分发回调"""
        self._on_task_dispatch = callback
    
    def on_task_cancel(self, callback: Callable[[TaskCancel], Awaitable[None]]):
        """注册任务取消回调"""
        self._on_task_cancel = callback
    
    async def _handle_task_dispatch(self, task: TaskDispatch):
        """处理任务分发"""
        if self._on_task_dispatch:
            await self._on_task_dispatch(task)
    
    async def _handle_task_cancel(self, cancel: TaskCancel):
        """处理任务取消"""
        if self._on_task_cancel:
            await self._on_task_cancel(cancel)
    
    async def _set_state(self, new_state: ConnectionState):
        """设置连接状态并发布事件"""
        old_state = self._state
        self._state = new_state
        
        if old_state != new_state:
            await event_bus.publish(ConnectionStateChanged(
                old_state=old_state,
                new_state=new_state,
            ))
    
    async def _buffer_message(
        self,
        message_type: MessageType,
        payload: Union[Heartbeat, List[LogEntry], TaskStatus],
    ):
        """缓冲消息"""
        message = BufferedMessage(
            message_type=message_type,
            payload=payload,
        )
        await self._message_buffer.add(message)
    
    def _start_reconnect_loop(self):
        """启动重连循环"""
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())
    
    async def _reconnect_loop(self):
        """
        重连循环
        
        使用指数退避算法进行重连尝试。
        
        Requirements: 7.1, 7.2
        """
        attempt = 0
        
        while self._running and not self.is_connected:
            try:
                # 计算延迟
                delay = self._backoff.next_delay()
                
                logger.info(f"将在 {delay:.1f} 秒后尝试重连 (尝试 #{attempt + 1})")
                
                # 发布重连尝试事件
                await event_bus.publish(ReconnectionAttempt(
                    attempt_number=attempt + 1,
                    delay_seconds=delay,
                    protocol=Protocol.GRPC,
                ))
                
                # 等待延迟
                await asyncio.sleep(delay)
                
                if not self._running:
                    break
                
                # 尝试重连
                await self._set_state(ConnectionState.RECONNECTING)
                
                try:
                    success = await self._transport.connect(self._config)
                    
                    if success:
                        await self._set_state(ConnectionState.CONNECTED)
                        self._backoff.reset()
                        
                        # 记录重连指标
                        self._transport.metrics.record_reconnection()
                        
                        # 重连成功，发送待处理消息
                        pending_count = await self._resend_pending_messages()
                        
                        # 发送立即心跳
                        await self._send_immediate_heartbeat()
                        
                        # 发布重连成功事件
                        await event_bus.publish(ReconnectionSuccess(
                            attempt_number=attempt + 1,
                            protocol=Protocol.GRPC,
                            pending_messages_count=pending_count,
                        ))
                        
                        logger.info(f"重连成功 (尝试 #{attempt + 1})，已发送 {pending_count} 条待处理消息")
                        break
                    else:
                        # 发布重连失败事件
                        next_delay = self._backoff.get_delay_for_attempt(self._backoff.attempt)
                        await event_bus.publish(ReconnectionFailed(
                            attempt_number=attempt + 1,
                            error="连接返回失败",
                            next_delay_seconds=next_delay,
                        ))
                        
                except Exception as e:
                    logger.warning(f"重连尝试 #{attempt + 1} 失败: {e}")
                    
                    # 发布重连失败事件
                    next_delay = self._backoff.get_delay_for_attempt(self._backoff.attempt)
                    await event_bus.publish(ReconnectionFailed(
                        attempt_number=attempt + 1,
                        error=str(e),
                        next_delay_seconds=next_delay,
                    ))
                
                attempt += 1
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"重连循环异常: {e}")
                await asyncio.sleep(5)
    
    async def _resend_pending_messages(self) -> int:
        """
        重发待处理消息
        
        Requirements: 7.3
        """
        messages = await self._message_buffer.get_all()
        sent_count = 0
        
        for message in messages:
            try:
                if message.message_type == MessageType.LOG_BATCH:
                    logs = message.payload
                    if isinstance(logs, list):
                        success = await self._transport.send_logs(logs)
                        if success:
                            sent_count += 1
                        else:
                            # 重新缓冲失败的消息
                            await self._message_buffer.add(message)
                            
                elif message.message_type == MessageType.TASK_STATUS:
                    status = message.payload
                    if isinstance(status, TaskStatus):
                        success = await self._transport.send_task_status(status)
                        if success:
                            sent_count += 1
                        else:
                            await self._message_buffer.add(message)
                            
            except Exception as e:
                logger.error(f"重发消息失败: {e}")
                # 重新缓冲失败的消息
                await self._message_buffer.add(message)
        
        return sent_count
    
    async def _send_immediate_heartbeat(self):
        """
        重连后立即发送心跳
        
        Requirements: 7.5
        """
        try:
            # 构建心跳消息
            import platform
            from ..domain.models import Metrics, OSInfo
            
            heartbeat = Heartbeat(
                node_id=self._config.node_id if self._config else "",
                status="online",
                metrics=Metrics(),
                os_info=OSInfo(
                    os_type=platform.system(),
                    os_version=platform.release(),
                    python_version=platform.python_version(),
                    machine_arch=platform.machine(),
                ),
                timestamp=datetime.now(),
                capabilities={},
            )
            
            await self._transport.send_heartbeat(heartbeat)
            logger.debug("重连后立即心跳已发送")
            
        except Exception as e:
            logger.warning(f"重连后发送心跳失败: {e}")
    
    def notify_disconnection(self):
        """
        通知连接断开
        
        由外部调用，触发重连逻辑
        """
        if self._running and self._state == ConnectionState.CONNECTED:
            asyncio.create_task(self._handle_disconnection())
    
    async def _handle_disconnection(self):
        """处理连接断开"""
        await self._set_state(ConnectionState.DISCONNECTED)
        self._start_reconnect_loop()
