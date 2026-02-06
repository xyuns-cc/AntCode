"""
批量日志发送器

将日志批量发送以提高效率，支持 backpressure。

Requirements: 9.5
"""

import asyncio
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Protocol

from loguru import logger

from antcode_worker.domain.models import LogEntry


class TransportProtocol(Protocol):
    """传输层协议"""

    async def send_log_batch(self, logs: list[Any]) -> bool:
        """批量发送日志"""
        ...

    @property
    def is_connected(self) -> bool:
        """是否已连接"""
        ...


class BackpressureState(str, Enum):
    """Backpressure 状态"""
    NORMAL = "normal"           # 正常
    WARNING = "warning"         # 警告（队列较满）
    CRITICAL = "critical"       # 临界（开始丢弃）
    BLOCKED = "blocked"         # 阻塞（停止接收）


@dataclass
class BatchConfig:
    """批量发送配置"""
    
    # 批次控制
    batch_size: int = 100                    # 每批最大条目数
    batch_timeout: float = 1.0               # 批次超时（秒）
    
    # 队列控制
    max_queue_size: int = 10000              # 最大队列大小
    warning_threshold: float = 0.7           # 警告阈值（70%）
    critical_threshold: float = 0.9          # 临界阈值（90%）
    
    # 发送控制
    max_retries: int = 3
    retry_delay: float = 0.5
    max_concurrent_batches: int = 3          # 最大并发批次
    
    # Backpressure
    drop_on_critical: bool = True            # 临界时丢弃新日志
    drop_priority: str = "oldest"            # 丢弃策略：oldest/newest


class BatchSender:
    """
    批量日志发送器
    
    将日志条目批量发送以提高效率。
    
    功能：
    - 批量发送
    - 队列管理
    - Backpressure 控制
    - 失败重试
    
    Requirements: 9.5
    """

    def __init__(
        self,
        run_id: str,
        transport: TransportProtocol,
        config: BatchConfig | None = None,
        on_backpressure: Callable[[BackpressureState], None] | None = None,
        on_batch_sent: Callable[[int, bool], None] | None = None,
    ):
        """
        初始化批量发送器
        
        Args:
            run_id: 运行 ID
            transport: 传输层实例
            config: 批量配置
            on_backpressure: Backpressure 状态变更回调
            on_batch_sent: 批次发送完成回调
        """
        self.run_id = run_id
        self._transport = transport
        self._config = config or BatchConfig()
        self._on_backpressure = on_backpressure
        self._on_batch_sent = on_batch_sent
        
        # 队列
        self._queue: deque[LogEntry] = deque(maxlen=self._config.max_queue_size)
        self._queue_lock = asyncio.Lock()
        
        # 状态
        self._running = False
        self._backpressure_state = BackpressureState.NORMAL
        
        # 任务
        self._send_task: asyncio.Task | None = None
        self._batch_semaphore = asyncio.Semaphore(self._config.max_concurrent_batches)
        
        # 统计
        self._total_queued = 0
        self._total_sent = 0
        self._total_failed = 0
        self._total_dropped = 0
        self._batches_sent = 0

    @property
    def queue_size(self) -> int:
        """当前队列大小"""
        return len(self._queue)

    @property
    def backpressure_state(self) -> BackpressureState:
        """当前 backpressure 状态"""
        return self._backpressure_state

    @property
    def is_connected(self) -> bool:
        """传输层是否已连接"""
        return self._transport.is_connected

    async def start(self) -> None:
        """启动批量发送器"""
        if self._running:
            return
        
        self._running = True
        self._send_task = asyncio.create_task(self._send_loop())
        
        logger.info(f"[{self.run_id}] 批量发送器已启动")

    async def stop(self) -> None:
        """停止批量发送器"""
        self._running = False
        
        # 停止发送任务
        if self._send_task and not self._send_task.done():
            self._send_task.cancel()
            try:
                await self._send_task
            except asyncio.CancelledError:
                pass
        
        # 发送剩余日志
        await self._flush_remaining()
        
        logger.info(f"[{self.run_id}] 批量发送器已停止")

    async def write(self, entry: LogEntry) -> bool:
        """
        写入日志条目到队列
        
        实现 LogSink 协议。
        
        Args:
            entry: 日志条目
            
        Returns:
            是否成功入队
        """
        if not self._running:
            return False
        
        # 检查 backpressure
        await self._update_backpressure_state()
        
        if self._backpressure_state == BackpressureState.BLOCKED:
            self._total_dropped += 1
            return False
        
        if (
            self._backpressure_state == BackpressureState.CRITICAL
            and self._config.drop_on_critical
        ):
            self._total_dropped += 1
            return False
        
        # 入队
        async with self._queue_lock:
            self._queue.append(entry)
            self._total_queued += 1
        
        return True

    async def flush(self) -> None:
        """刷新队列"""
        await self._flush_remaining()

    async def _update_backpressure_state(self) -> None:
        """更新 backpressure 状态"""
        queue_ratio = len(self._queue) / self._config.max_queue_size
        
        if queue_ratio >= 1.0:
            new_state = BackpressureState.BLOCKED
        elif queue_ratio >= self._config.critical_threshold:
            new_state = BackpressureState.CRITICAL
        elif queue_ratio >= self._config.warning_threshold:
            new_state = BackpressureState.WARNING
        else:
            new_state = BackpressureState.NORMAL
        
        if new_state != self._backpressure_state:
            old_state = self._backpressure_state
            self._backpressure_state = new_state
            
            logger.debug(
                f"[{self.run_id}] Backpressure 状态变更: "
                f"{old_state.value} -> {new_state.value}"
            )
            
            if self._on_backpressure:
                try:
                    self._on_backpressure(new_state)
                except Exception:
                    pass

    async def _send_loop(self) -> None:
        """发送循环"""
        while self._running:
            try:
                # 等待批次超时或队列满
                await asyncio.sleep(self._config.batch_timeout)
                
                if not self._running:
                    break
                
                # 发送批次
                await self._send_batch()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[{self.run_id}] 批量发送循环异常: {e}")

    async def _send_batch(self) -> None:
        """发送一个批次"""
        if not self._queue:
            return
        
        # 获取批次
        async with self._queue_lock:
            batch_size = min(len(self._queue), self._config.batch_size)
            batch = [self._queue.popleft() for _ in range(batch_size)]
        
        if not batch:
            return
        
        # 使用信号量控制并发
        async with self._batch_semaphore:
            success = await self._send_batch_with_retry(batch)
            
            if success:
                self._total_sent += len(batch)
                self._batches_sent += 1
            else:
                self._total_failed += len(batch)
            
            if self._on_batch_sent:
                try:
                    self._on_batch_sent(len(batch), success)
                except Exception:
                    pass

    async def _send_batch_with_retry(self, batch: list[LogEntry]) -> bool:
        """带重试的批量发送"""
        # 构建日志消息列表
        log_messages = [self._build_log_message(entry) for entry in batch]
        
        for attempt in range(self._config.max_retries):
            try:
                # 检查连接
                if not self.is_connected:
                    await asyncio.sleep(self._config.retry_delay)
                    continue
                
                # 发送
                success = await self._transport.send_log_batch(log_messages)
                
                if success:
                    return True
                
            except Exception as e:
                logger.debug(
                    f"[{self.run_id}] 批量发送失败 (attempt {attempt + 1}): {e}"
                )
            
            # 重试延迟
            if attempt < self._config.max_retries - 1:
                await asyncio.sleep(self._config.retry_delay)
        
        return False

    def _build_log_message(self, entry: LogEntry) -> Any:
        """构建日志消息"""
        from antcode_worker.transport.base import LogMessage
        
        return LogMessage(
            execution_id=entry.run_id,
            log_type=entry.stream.value,
            content=entry.content,
            timestamp=entry.timestamp,
            sequence=entry.seq,
        )

    async def _flush_remaining(self) -> None:
        """发送剩余的日志"""
        while self._queue:
            await self._send_batch()

    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            "run_id": self.run_id,
            "running": self._running,
            "queue_size": len(self._queue),
            "max_queue_size": self._config.max_queue_size,
            "backpressure_state": self._backpressure_state.value,
            "total_queued": self._total_queued,
            "total_sent": self._total_sent,
            "total_failed": self._total_failed,
            "total_dropped": self._total_dropped,
            "batches_sent": self._batches_sent,
        }


class BatchSink:
    """
    批量日志 Sink
    
    包装 BatchSender 以实现 LogSink 协议。
    """

    def __init__(self, sender: BatchSender):
        """
        初始化
        
        Args:
            sender: 批量发送器
        """
        self._sender = sender

    async def write(self, entry: LogEntry) -> bool:
        """写入日志条目"""
        return await self._sender.write(entry)

    async def flush(self) -> None:
        """刷新"""
        await self._sender.flush()
