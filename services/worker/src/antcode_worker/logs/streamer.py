"""
日志流式捕获器

负责实时捕获 stdout/stderr 并转换为 LogEntry(seq)。

Requirements: 9.2
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from loguru import logger

from antcode_worker.domain.enums import LogStream
from antcode_worker.domain.models import LogEntry


class LogSink(Protocol):
    """日志接收器协议"""

    async def write(self, entry: LogEntry) -> bool:
        """写入日志条目"""
        ...


@dataclass
class StreamCapture:
    """
    流捕获结果
    
    包含捕获的内容和元数据。
    """
    content: str
    stream: LogStream
    timestamp: datetime
    line_count: int = 1


class LogStreamer:
    """
    日志流式捕获器
    
    实时捕获 stdout/stderr 并转换为 LogEntry(seq)。
    
    功能：
    - 从 asyncio.StreamReader 捕获输出
    - 自动分配序列号
    - 转换为 LogEntry 格式
    - 支持多个 sink（实时/spool/batch）
    
    Requirements: 9.2
    """

    def __init__(
        self,
        run_id: str,
        sinks: list[LogSink] | None = None,
        on_entry: Callable[[LogEntry], None] | None = None,
    ):
        """
        初始化日志流式捕获器
        
        Args:
            run_id: 运行 ID
            sinks: 日志接收器列表
            on_entry: 日志条目回调
        """
        self.run_id = run_id
        self._sinks = sinks or []
        self._on_entry = on_entry
        
        # 序列号管理
        self._seq_counter = 0
        self._seq_lock = asyncio.Lock()
        
        # 统计
        self._stdout_lines = 0
        self._stderr_lines = 0
        self._total_bytes = 0
        
        # 状态
        self._running = False
        self._capture_tasks: list[asyncio.Task] = []

    @property
    def stdout_lines(self) -> int:
        """stdout 行数"""
        return self._stdout_lines

    @property
    def stderr_lines(self) -> int:
        """stderr 行数"""
        return self._stderr_lines

    @property
    def total_bytes(self) -> int:
        """总字节数"""
        return self._total_bytes

    def add_sink(self, sink: LogSink) -> None:
        """添加日志接收器"""
        self._sinks.append(sink)

    def remove_sink(self, sink: LogSink) -> None:
        """移除日志接收器"""
        if sink in self._sinks:
            self._sinks.remove(sink)

    async def _next_seq(self) -> int:
        """获取下一个序列号"""
        async with self._seq_lock:
            seq = self._seq_counter
            self._seq_counter += 1
            return seq

    async def capture_stream(
        self,
        reader: asyncio.StreamReader,
        stream: LogStream,
    ) -> None:
        """
        捕获单个流
        
        Args:
            reader: 异步流读取器
            stream: 流类型（stdout/stderr）
        """
        self._running = True
        
        try:
            while True:
                try:
                    line = await reader.readline()
                    if not line:
                        break
                    
                    content = line.decode("utf-8", errors="replace").rstrip("\n\r")
                    await self._process_line(content, stream)
                    
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"[{self.run_id}] 捕获流异常: {e}")
                    break
        finally:
            self._running = False

    async def capture_both(
        self,
        stdout: asyncio.StreamReader,
        stderr: asyncio.StreamReader,
    ) -> None:
        """
        同时捕获 stdout 和 stderr
        
        Args:
            stdout: stdout 流读取器
            stderr: stderr 流读取器
        """
        self._running = True
        
        stdout_task = asyncio.create_task(
            self.capture_stream(stdout, LogStream.STDOUT)
        )
        stderr_task = asyncio.create_task(
            self.capture_stream(stderr, LogStream.STDERR)
        )
        
        self._capture_tasks = [stdout_task, stderr_task]
        
        try:
            await asyncio.gather(stdout_task, stderr_task)
        except asyncio.CancelledError:
            for task in self._capture_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*self._capture_tasks, return_exceptions=True)
        finally:
            self._running = False
            self._capture_tasks.clear()

    async def _process_line(self, content: str, stream: LogStream) -> None:
        """
        处理单行日志
        
        Args:
            content: 日志内容
            stream: 流类型
        """
        if not content:
            return
        
        # 更新统计
        self._total_bytes += len(content.encode("utf-8"))
        if stream == LogStream.STDOUT:
            self._stdout_lines += 1
        else:
            self._stderr_lines += 1
        
        # 创建 LogEntry
        seq = await self._next_seq()
        entry = LogEntry(
            run_id=self.run_id,
            stream=stream,
            content=content,
            seq=seq,
            timestamp=datetime.now(),
            level="INFO" if stream == LogStream.STDOUT else "ERROR",
        )
        
        # 回调
        if self._on_entry:
            try:
                self._on_entry(entry)
            except Exception as e:
                logger.error(f"[{self.run_id}] 日志回调异常: {e}")
        
        # 发送到所有 sink
        await self._dispatch_to_sinks(entry)

    async def _dispatch_to_sinks(self, entry: LogEntry) -> None:
        """
        分发日志到所有 sink
        
        Args:
            entry: 日志条目
        """
        for sink in self._sinks:
            try:
                await sink.write(entry)
            except Exception as e:
                logger.error(f"[{self.run_id}] 写入 sink 失败: {e}")

    async def write_system_log(
        self,
        content: str,
        level: str = "INFO",
    ) -> None:
        """
        写入系统日志
        
        Args:
            content: 日志内容
            level: 日志级别
        """
        seq = await self._next_seq()
        entry = LogEntry(
            run_id=self.run_id,
            stream=LogStream.SYSTEM,
            content=content,
            seq=seq,
            timestamp=datetime.now(),
            level=level,
            source="worker",
        )
        
        # 回调
        if self._on_entry:
            try:
                self._on_entry(entry)
            except Exception as e:
                logger.error(f"[{self.run_id}] 日志回调异常: {e}")
        
        await self._dispatch_to_sinks(entry)

    async def flush(self) -> None:
        """刷新所有 sink"""
        for sink in self._sinks:
            if hasattr(sink, "flush"):
                try:
                    await sink.flush()
                except Exception as e:
                    logger.error(f"[{self.run_id}] 刷新 sink 失败: {e}")

    async def stop(self) -> None:
        """停止捕获"""
        self._running = False
        
        # 取消所有捕获任务
        for task in self._capture_tasks:
            if not task.done():
                task.cancel()
        
        if self._capture_tasks:
            await asyncio.gather(*self._capture_tasks, return_exceptions=True)
        
        # 刷新所有 sink
        await self.flush()

    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            "run_id": self.run_id,
            "stdout_lines": self._stdout_lines,
            "stderr_lines": self._stderr_lines,
            "total_bytes": self._total_bytes,
            "seq_counter": self._seq_counter,
            "running": self._running,
            "sink_count": len(self._sinks),
        }


async def iter_stream(
    reader: asyncio.StreamReader,
    stream: LogStream,
    run_id: str,
) -> AsyncIterator[LogEntry]:
    """
    异步迭代流内容
    
    Args:
        reader: 异步流读取器
        stream: 流类型
        run_id: 运行 ID
        
    Yields:
        LogEntry 对象
    """
    seq = 0
    while True:
        try:
            line = await reader.readline()
            if not line:
                break
            
            content = line.decode("utf-8", errors="replace").rstrip("\n\r")
            if content:
                yield LogEntry(
                    run_id=run_id,
                    stream=stream,
                    content=content,
                    seq=seq,
                    timestamp=datetime.now(),
                )
                seq += 1
                
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[{run_id}] 迭代流异常: {e}")
            break
