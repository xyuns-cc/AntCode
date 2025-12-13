"""
日志缓冲器模块

特性:
- 按 execution_id 分组缓冲日志
- 达到阈值或定时触发批量发送
- gzip 压缩批量发送
- 发送失败时保留日志重试
- 缓冲区溢出时丢弃最旧日志
- 任务完成时立即刷新

Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7
"""
import asyncio
import gzip
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from typing import Callable, Coroutine, Any, Dict, Deque, List, Optional

from loguru import logger

from ..utils.serialization import Serializer


@dataclass
class LogBufferEntry:
    """日志缓冲条目"""
    execution_id: str
    log_type: str  # "stdout" | "stderr"
    content: str
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return asdict(self)


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


class LogBuffer:
    """
    日志缓冲器 - Worker 端日志批量上报
    
    策略:
    1. 日志先进入内存缓冲区
    2. 达到 max_size 条或 flush_interval 秒后批量发送
    3. 发送时使用 gzip 压缩
    4. 发送失败时保留日志，下次重试
    5. 缓冲区超过 max_buffer_lines 时丢弃最旧日志
    6. 任务完成时立即刷新该任务的所有日志
    """

    def __init__(
        self,
        max_size: int = 50,
        flush_interval: float = 2.0,
        max_buffer_lines: int = 500,
        compress: bool = True,
        send_func: Optional[Callable[[List[Dict[str, Any]], bool], Coroutine[Any, Any, bool]]] = None,
    ):
        """
        初始化日志缓冲器
        
        Args:
            max_size: 触发刷新的日志条数阈值
            flush_interval: 定时刷新间隔（秒）
            max_buffer_lines: 缓冲区最大行数，超过时丢弃最旧日志
            compress: 是否启用 gzip 压缩
            send_func: 发送函数，签名为 async (logs: List[Dict], compressed: bool) -> bool
        """
        self._max_size = max_size
        self._flush_interval = flush_interval
        self._max_buffer_lines = max_buffer_lines
        self._compress = compress
        self._send_func = send_func

        # 按 execution_id 分组的缓冲区
        self._buffers: Dict[str, Deque[LogBufferEntry]] = {}
        self._buffer_lock = asyncio.Lock()

        # 全局缓冲区（用于统计总行数）
        self._total_lines = 0

        # 统计信息
        self._stats = LogBufferStats()

        # 后台任务
        self._flush_task: Optional[asyncio.Task] = None
        self._running = False
        self._last_flush_time = time.time()

    @property
    def max_size(self) -> int:
        """获取触发刷新的阈值"""
        return self._max_size

    @property
    def flush_interval(self) -> float:
        """获取刷新间隔"""
        return self._flush_interval

    @property
    def max_buffer_lines(self) -> int:
        """获取最大缓冲行数"""
        return self._max_buffer_lines

    @property
    def total_lines(self) -> int:
        """获取当前缓冲区总行数"""
        return self._total_lines

    def set_send_func(self, send_func: Callable[[List[Dict[str, Any]], bool], Coroutine[Any, Any, bool]]):
        """设置发送函数"""
        self._send_func = send_func

    async def add(self, execution_id: str, log_type: str, content: str) -> None:
        """
        添加日志行到缓冲区
        
        Args:
            execution_id: 执行 ID
            log_type: 日志类型 ("stdout" | "stderr")
            content: 日志内容
        """
        entry = LogBufferEntry(
            execution_id=execution_id,
            log_type=log_type,
            content=content,
        )

        async with self._buffer_lock:
            # 获取或创建该 execution_id 的缓冲区
            if execution_id not in self._buffers:
                self._buffers[execution_id] = deque()

            buffer = self._buffers[execution_id]

            # 检查是否需要丢弃最旧日志（全局限制）
            while self._total_lines >= self._max_buffer_lines:
                dropped = self._drop_oldest_log()
                if not dropped:
                    break

            # 添加新日志
            buffer.append(entry)
            self._total_lines += 1
            self._stats.total_added += 1

            # 检查是否需要触发刷新（该 execution_id 的缓冲区达到阈值）
            should_flush = len(buffer) >= self._max_size

        # 在锁外触发刷新
        if should_flush:
            asyncio.create_task(self.flush(execution_id))

    def _drop_oldest_log(self) -> bool:
        """
        丢弃最旧的日志条目
        
        Returns:
            是否成功丢弃
        """
        # 找到最旧的日志条目
        oldest_exec_id = None
        oldest_time = float('inf')

        for exec_id, buffer in self._buffers.items():
            if buffer and buffer[0].timestamp < oldest_time:
                oldest_time = buffer[0].timestamp
                oldest_exec_id = exec_id

        if oldest_exec_id is None:
            return False

        # 丢弃最旧的日志
        buffer = self._buffers[oldest_exec_id]
        if buffer:
            buffer.popleft()
            self._total_lines -= 1
            self._stats.total_dropped += 1

            # 如果缓冲区为空，删除该 execution_id
            if not buffer:
                del self._buffers[oldest_exec_id]

            return True

        return False

    async def flush(self, execution_id: Optional[str] = None) -> None:
        """
        刷新缓冲区，发送到 Master
        
        Args:
            execution_id: 指定要刷新的 execution_id，None 表示刷新所有
        """
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

            # 如果缓冲区为空，删除该 execution_id
            if not buffer:
                del self._buffers[execution_id]

        # 发送日志
        await self._send_logs(logs)

    async def flush_all(self) -> None:
        """刷新所有缓冲区"""
        async with self._buffer_lock:
            if not self._buffers:
                return

            # 收集所有日志
            all_logs: List[LogBufferEntry] = []
            for buffer in self._buffers.values():
                all_logs.extend(buffer)

            # 清空所有缓冲区
            self._buffers.clear()
            self._total_lines = 0

        if all_logs:
            await self._send_logs(all_logs)

    async def _send_logs(self, logs: List[LogBufferEntry]) -> bool:
        """
        发送日志批次
        
        Args:
            logs: 日志条目列表
            
        Returns:
            是否发送成功
        """
        if not logs:
            return True

        if not self._send_func:
            logger.warning("LogBuffer: 未设置发送函数，日志将被丢弃")
            return False

        # 转换为字典格式
        log_dicts = [
            {
                "execution_id": log.execution_id,
                "log_type": log.log_type,
                "content": log.content,
                "timestamp": log.timestamp,
            }
            for log in logs
        ]

        try:
            success = await self._send_func(log_dicts, self._compress)

            if success:
                self._stats.total_flushed += len(logs)
                self._stats.flush_count += 1
                self._stats.last_flush_time = time.time()
                self._last_flush_time = time.time()
                return True
            else:
                # 发送失败，放回缓冲区
                await self._restore_logs(logs)
                self._stats.failed_flush_count += 1
                return False

        except Exception as e:
            logger.error(f"LogBuffer: 发送日志失败: {e}")
            # 发送失败，放回缓冲区
            await self._restore_logs(logs)
            self._stats.failed_flush_count += 1
            return False

    async def _restore_logs(self, logs: List[LogBufferEntry]) -> None:
        """将日志放回缓冲区（发送失败时）"""
        async with self._buffer_lock:
            for log in logs:
                # 检查是否超过最大行数
                if self._total_lines >= self._max_buffer_lines:
                    # 丢弃最旧的日志
                    self._drop_oldest_log()

                # 放回缓冲区
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

        # 停止后台任务
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

                # 检查是否需要刷新（距离上次刷新超过 flush_interval）
                if time.time() - self._last_flush_time >= self._flush_interval:
                    await self.flush_all()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"LogBuffer: 刷新循环异常: {e}")

    async def flush_execution(self, execution_id: str) -> None:
        """
        立即刷新指定执行的所有日志（任务完成时调用）
        
        Args:
            execution_id: 执行 ID
        """
        await self._flush_execution(execution_id)

    def get_buffer_size(self, execution_id: Optional[str] = None) -> int:
        """
        获取缓冲区大小
        
        Args:
            execution_id: 指定 execution_id，None 表示获取总大小
            
        Returns:
            缓冲区中的日志行数
        """
        if execution_id:
            buffer = self._buffers.get(execution_id)
            return len(buffer) if buffer else 0
        return self._total_lines

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
            "max_buffer_lines": self._max_buffer_lines,
            "flush_interval": self._flush_interval,
            "compress_enabled": self._compress,
        }


def compress_logs(logs: List[Dict[str, Any]]) -> bytes:
    """
    压缩日志数据
    
    使用 ujson 进行高性能 JSON 序列化，然后使用 gzip 压缩
    
    Args:
        logs: 日志字典列表
        
    Returns:
        gzip 压缩后的字节数据
    """
    json_data = Serializer.to_json(logs)
    return gzip.compress(json_data.encode('utf-8'))


def decompress_logs(data: bytes) -> List[Dict[str, Any]]:
    """
    解压日志数据
    
    使用 gzip 解压后，使用 ujson 进行高性能 JSON 反序列化
    
    Args:
        data: gzip 压缩的字节数据
        
    Returns:
        日志字典列表
    """
    json_data = gzip.decompress(data).decode('utf-8')
    return Serializer.from_json(json_data)
