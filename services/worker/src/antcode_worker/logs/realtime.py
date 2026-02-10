"""
实时日志发送器

通过 Transport 实时发送日志到 Master/Gateway。

Requirements: 9.4
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from loguru import logger

from antcode_worker.domain.models import LogEntry


class TransportProtocol(Protocol):
    """传输层协议"""

    async def send_log(self, log: Any) -> bool:
        """发送日志"""
        ...

    @property
    def is_connected(self) -> bool:
        """是否已连接"""
        ...


@dataclass
class RealtimeConfig:
    """实时发送配置"""
    
    # 发送控制
    enabled: bool = True
    max_retries: int = 3
    retry_delay: float = 0.1
    
    # 速率限制
    max_entries_per_second: int = 1000
    
    # 连接检查
    check_connection: bool = True


class RealtimeSender:
    """
    实时日志发送器
    
    将日志条目实时发送到 Transport 层。
    
    功能：
    - 实时发送日志
    - 连接状态检查
    - 失败重试
    - 速率限制
    
    Requirements: 9.4
    """

    def __init__(
        self,
        run_id: str,
        transport: TransportProtocol,
        config: RealtimeConfig | None = None,
        on_send_failure: Callable[[LogEntry, str], None] | None = None,
    ):
        """
        初始化实时发送器
        
        Args:
            run_id: 运行 ID
            transport: 传输层实例
            config: 发送配置
            on_send_failure: 发送失败回调
        """
        self.run_id = run_id
        self._transport = transport
        self._config = config or RealtimeConfig()
        self._on_send_failure = on_send_failure
        
        # 状态
        self._enabled = self._config.enabled
        self._running = False
        
        # 速率限制
        self._send_count = 0
        self._last_reset = datetime.now()
        self._rate_lock = asyncio.Lock()
        
        # 统计
        self._total_sent = 0
        self._total_failed = 0
        self._total_dropped = 0

    @property
    def enabled(self) -> bool:
        """是否启用"""
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        """设置启用状态"""
        self._enabled = value

    @property
    def is_connected(self) -> bool:
        """传输层是否已连接"""
        if not self._config.check_connection:
            return True
        return self._transport.is_connected

    async def start(self) -> None:
        """启动发送器"""
        self._running = True
        logger.info(f"[{self.run_id}] 实时发送器已启动")

    async def stop(self) -> None:
        """停止发送器"""
        self._running = False
        logger.info(f"[{self.run_id}] 实时发送器已停止")

    async def write(self, entry: LogEntry) -> bool:
        """
        发送日志条目
        
        实现 LogSink 协议。
        
        Args:
            entry: 日志条目
            
        Returns:
            是否发送成功
        """
        if not self._enabled or not self._running:
            logger.debug(f"[{self.run_id}] 实时发送跳过: enabled={self._enabled}, running={self._running}")
            return False
        
        # 检查连接
        if not self.is_connected:
            self._total_dropped += 1
            logger.debug(f"[{self.run_id}] 实时发送跳过: 未连接")
            return False
        
        # 速率限制
        if not await self._check_rate_limit():
            self._total_dropped += 1
            logger.debug(f"[{self.run_id}] 实时发送跳过: 速率限制")
            return False
        
        # 发送
        return await self._send_with_retry(entry)

    async def _check_rate_limit(self) -> bool:
        """检查速率限制"""
        async with self._rate_lock:
            now = datetime.now()
            
            # 每秒重置计数
            if (now - self._last_reset).total_seconds() >= 1.0:
                self._send_count = 0
                self._last_reset = now
            
            if self._send_count >= self._config.max_entries_per_second:
                return False
            
            self._send_count += 1
            return True

    async def _send_with_retry(self, entry: LogEntry) -> bool:
        """带重试的发送"""
        last_error = ""
        
        for attempt in range(self._config.max_retries):
            try:
                # 构建日志消息
                log_message = self._build_log_message(entry)
                
                logger.debug(f"[{self.run_id}] 发送日志: seq={entry.seq}, stream={entry.stream.value}, content_len={len(entry.content)}")
                
                # 发送
                success = await self._transport.send_log(log_message)
                
                if success:
                    self._total_sent += 1
                    logger.debug(f"[{self.run_id}] 日志发送成功: seq={entry.seq}")
                    return True
                
                last_error = "Transport returned False"
                logger.debug(f"[{self.run_id}] 日志发送失败: Transport returned False")
                
            except Exception as e:
                last_error = str(e)
                logger.debug(
                    f"[{self.run_id}] 发送日志失败 (attempt {attempt + 1}): {e}"
                )
            
            # 重试延迟
            if attempt < self._config.max_retries - 1:
                await asyncio.sleep(self._config.retry_delay)
        
        # 所有重试失败
        self._total_failed += 1
        
        if self._on_send_failure:
            try:
                self._on_send_failure(entry, last_error)
            except Exception:
                pass
        
        return False

    def _build_log_message(self, entry: LogEntry) -> Any:
        """
        构建日志消息
        
        Args:
            entry: 日志条目
            
        Returns:
            Transport 可接受的日志消息格式
        """
        # 使用 Transport 的 LogMessage 格式
        from antcode_worker.transport.base import LogMessage
        
        return LogMessage(
            run_id=entry.run_id,
            log_type=entry.stream.value,
            content=entry.content,
            timestamp=entry.timestamp,
            sequence=entry.seq,
        )

    async def flush(self) -> None:
        """刷新（实时发送无需刷新）"""
        pass

    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            "run_id": self.run_id,
            "enabled": self._enabled,
            "running": self._running,
            "connected": self.is_connected,
            "total_sent": self._total_sent,
            "total_failed": self._total_failed,
            "total_dropped": self._total_dropped,
            "current_rate": self._send_count,
        }


class RealtimeSink:
    """
    实时日志 Sink
    
    包装 RealtimeSender 以实现 LogSink 协议。
    """

    def __init__(self, sender: RealtimeSender):
        """
        初始化
        
        Args:
            sender: 实时发送器
        """
        self._sender = sender

    async def write(self, entry: LogEntry) -> bool:
        """写入日志条目"""
        return await self._sender.write(entry)

    async def flush(self) -> None:
        """刷新"""
        await self._sender.flush()
