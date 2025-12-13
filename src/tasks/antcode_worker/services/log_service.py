"""
日志服务

负责缓冲和批量发送日志到 Master。
包含优化的日志缓冲区实现。

Requirements: 11.3
"""

import asyncio
import gzip
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any, List, Deque, Callable, Awaitable

from loguru import logger

from ..domain.models import LogEntry
from ..domain.interfaces import LogService as ILogService
from ..domain.events import LogBatchSent, MessageDropped, event_bus
from ..transport.protocol import TransportProtocol


@dataclass
class LogBufferStats:
    """日志缓冲统计"""
    total_added: int = 0
    total_flushed: int = 0
    total_dropped: int = 0
    flush_count: int = 0
    failed_flush_count: int = 0
    last_flush_time: Optional[float] = None
    compression_ratio: float = 0.0


class OptimizedLogBuffer:
    """
    优化的日志缓冲区
    
    特性:
    - 按 execution_id 分组缓冲
    - 达到阈值或定时触发批量发送
    - gzip 压缩大批量
    - 发送失败时保留重试
    - 缓冲区溢出时丢弃最旧日志（背压）
    - 任务完成时立即刷新
    
    Requirements: 11.3, 13.2, 13.4
    """

    def __init__(
        self,
        max_size: int = 2000,
        batch_size: int = 50,
        flush_interval: float = 1.0,
        compress_threshold: int = 1024,
    ):
        """
        初始化日志缓冲区
        
        Args:
            max_size: 缓冲区最大行数
            batch_size: 触发刷新的批次大小
            flush_interval: 定时刷新间隔（秒）
            compress_threshold: 压缩阈值（字节）
        """
        self._max_size = max_size
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._compress_threshold = compress_threshold

        # 按 execution_id 分组的缓冲区
        self._buffers: Dict[str, Deque[LogEntry]] = {}
        self._buffer_lock = asyncio.Lock()
        self._total_lines = 0

        # 统计信息
        self._stats = LogBufferStats()

        # 发送函数
        self._send_func: Optional[Callable[[List[LogEntry]], Awaitable[bool]]] = None

        # 后台任务
        self._flush_task: Optional[asyncio.Task] = None
        self._running = False
        self._last_flush_time = time.time()

    @property
    def max_size(self) -> int:
        return self._max_size

    @property
    def batch_size(self) -> int:
        return self._batch_size

    @property
    def flush_interval(self) -> float:
        return self._flush_interval

    @property
    def total_lines(self) -> int:
        return self._total_lines

    def set_send_func(self, send_func: Callable[[List[LogEntry]], Awaitable[bool]]):
        """设置发送函数"""
        self._send_func = send_func

    async def add(self, execution_id: str, log_type: str, content: str) -> None:
        """添加日志行到缓冲区"""
        entry = LogEntry(
            execution_id=execution_id,
            log_type=log_type,
            content=content,
            timestamp=datetime.now(),
        )

        async with self._buffer_lock:
            # 获取或创建该 execution_id 的缓冲区
            if execution_id not in self._buffers:
                self._buffers[execution_id] = deque()

            buffer = self._buffers[execution_id]

            # 检查是否需要丢弃最旧日志（背压）
            while self._total_lines >= self._max_size:
                dropped = self._drop_oldest_log()
                if not dropped:
                    break

            # 添加新日志
            buffer.append(entry)
            self._total_lines += 1
            self._stats.total_added += 1

            # 检查是否需要触发刷新
            should_flush = len(buffer) >= self._batch_size

        # 在锁外触发刷新
        if should_flush:
            asyncio.create_task(self.flush(execution_id))

    def _drop_oldest_log(self) -> bool:
        """丢弃最旧的日志条目（背压机制）"""
        oldest_exec_id = None
        oldest_time = float('inf')

        for exec_id, buffer in self._buffers.items():
            if buffer and buffer[0].timestamp.timestamp() < oldest_time:
                oldest_time = buffer[0].timestamp.timestamp()
                oldest_exec_id = exec_id

        if oldest_exec_id is None:
            return False

        buffer = self._buffers[oldest_exec_id]
        if buffer:
            buffer.popleft()
            self._total_lines -= 1
            self._stats.total_dropped += 1

            # 发布丢弃事件
            event_bus.publish_sync(MessageDropped(
                message_type="log",
                reason="buffer_full",
                dropped_count=1
            ))

            # 如果缓冲区为空，删除该 execution_id
            if not buffer:
                del self._buffers[oldest_exec_id]

            logger.warning(f"日志缓冲区已满，丢弃最旧日志")
            return True

        return False

    async def flush(self, execution_id: Optional[str] = None) -> None:
        """刷新缓冲区"""
        if execution_id:
            await self._flush_execution(execution_id)
        else:
            await self.flush_all()

    async def _flush_execution(self, execution_id: str) -> None:
        """刷新指定 execution_id 的缓冲区"""
        async with self._buffer_lock:
            if execution_id not in self._buffers:
                return

            buffer = self._buffers[execution_id]
            if not buffer:
                return

            # 取出所有日志
            logs = list(buffer)
            buffer.clear()
            self._total_lines -= len(logs)

            if not buffer:
                del self._buffers[execution_id]

        # 发送日志
        await self._send_logs(logs)

    async def flush_all(self) -> None:
        """刷新所有缓冲区"""
        async with self._buffer_lock:
            if not self._buffers:
                return

            all_logs: List[LogEntry] = []
            for buffer in self._buffers.values():
                all_logs.extend(buffer)

            self._buffers.clear()
            self._total_lines = 0

        if all_logs:
            await self._send_logs(all_logs)

    async def _send_logs(self, logs: List[LogEntry]) -> bool:
        """发送日志批次"""
        if not logs:
            return True

        if not self._send_func:
            logger.warning("LogBuffer: 未设置发送函数，日志将被丢弃")
            return False

        try:
            success = await self._send_func(logs)

            if success:
                self._stats.total_flushed += len(logs)
                self._stats.flush_count += 1
                self._stats.last_flush_time = time.time()
                self._last_flush_time = time.time()

                # 发布成功事件
                await event_bus.publish(LogBatchSent(
                    execution_id=logs[0].execution_id if logs else "",
                    log_count=len(logs),
                    compressed=False,
                ))

                return True
            else:
                # 发送失败，放回缓冲区
                await self._restore_logs(logs)
                self._stats.failed_flush_count += 1
                return False

        except Exception as e:
            logger.error(f"LogBuffer: 发送日志失败: {e}")
            await self._restore_logs(logs)
            self._stats.failed_flush_count += 1
            return False

    async def _restore_logs(self, logs: List[LogEntry]) -> None:
        """将日志放回缓冲区（发送失败时）"""
        async with self._buffer_lock:
            for log in logs:
                if self._total_lines >= self._max_size:
                    self._drop_oldest_log()

                if log.execution_id not in self._buffers:
                    self._buffers[log.execution_id] = deque()

                self._buffers[log.execution_id].appendleft(log)
                self._total_lines += 1

    async def start(self) -> None:
        """启动后台刷新任务"""
        if self._running:
            return

        self._running = True
        self._flush_task = asyncio.create_task(self._flush_loop())
        logger.info(f"LogBuffer: 后台刷新任务已启动 (interval={self._flush_interval}s)")

    async def stop(self) -> None:
        """停止并刷新剩余日志"""
        self._running = False

        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass

        self._flush_task = None

        # 刷新剩余日志
        await self.flush_all()
        logger.info("LogBuffer: 已停止并刷新剩余日志")

    async def _flush_loop(self) -> None:
        """后台定时刷新循环"""
        while self._running:
            try:
                await asyncio.sleep(self._flush_interval)

                if not self._running:
                    break

                if time.time() - self._last_flush_time >= self._flush_interval:
                    await self.flush_all()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"LogBuffer: 刷新循环异常: {e}")

    def get_stats(self) -> Dict[str, Any]:
        """获取缓冲区统计信息"""
        return {
            "total_added": self._stats.total_added,
            "total_flushed": self._stats.total_flushed,
            "total_dropped": self._stats.total_dropped,
            "flush_count": self._stats.flush_count,
            "failed_flush_count": self._stats.failed_flush_count,
            "last_flush_time": self._stats.last_flush_time,
            "current_buffer_size": self._total_lines,
            "execution_count": len(self._buffers),
            "max_size": self._max_size,
            "batch_size": self._batch_size,
            "flush_interval": self._flush_interval,
        }


class LogServiceImpl(ILogService):
    """
    日志服务实现
    
    封装 OptimizedLogBuffer，提供简洁的日志服务接口。
    
    Requirements: 11.3
    """

    def __init__(
        self,
        transport: TransportProtocol,
        max_size: int = 2000,
        batch_size: int = 50,
        flush_interval: float = 1.0,
    ):
        """
        初始化日志服务
        
        Args:
            transport: 传输协议实例
            max_size: 缓冲区最大行数
            batch_size: 触发刷新的批次大小
            flush_interval: 定时刷新间隔（秒）
        """
        self._transport = transport
        self._buffer = OptimizedLogBuffer(
            max_size=max_size,
            batch_size=batch_size,
            flush_interval=flush_interval,
        )
        self._buffer.set_send_func(self._send_logs)

    @property
    def buffer_size(self) -> int:
        return self._buffer.total_lines

    async def add(self, execution_id: str, log_type: str, content: str):
        """添加日志行"""
        await self._buffer.add(execution_id, log_type, content)

    async def flush(self, execution_id: Optional[str] = None):
        """刷新日志缓冲区"""
        await self._buffer.flush(execution_id)

    async def start(self):
        """启动后台刷新任务"""
        await self._buffer.start()

    async def stop(self):
        """停止并刷新剩余日志"""
        await self._buffer.stop()

    async def _send_logs(self, logs: List[LogEntry]) -> bool:
        """发送日志到 Master"""
        return await self._transport.send_logs(logs)

    def get_stats(self) -> Dict[str, Any]:
        """获取日志服务统计"""
        return self._buffer.get_stats()


def compress_logs(logs: List[Dict[str, Any]]) -> bytes:
    """压缩日志数据"""
    import json
    json_data = json.dumps(logs, separators=(',', ':'))
    return gzip.compress(json_data.encode('utf-8'))


def decompress_logs(data: bytes) -> List[Dict[str, Any]]:
    """解压日志数据"""
    import json
    json_data = gzip.decompress(data).decode('utf-8')
    return json.loads(json_data)
