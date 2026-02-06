"""
日志流式传输器

负责实时推送日志到 Gateway/Redis，由 Web API 订阅转发。
"""

import asyncio
import contextlib
import time
from typing import Any, Protocol

from loguru import logger


class MessageSender(Protocol):
    """消息发送器协议"""

    async def send_message(self, message: Any) -> bool:
        """发送消息到 Gateway/Redis"""
        ...


class LogStreamer:
    """
    日志流式传输器

    通过传输层发送实时日志消息。

    功能：
    - enable(): 开启实时推送
    - disable(): 关闭实时推送
    - push(): 推送实时日志内容
    """

    def __init__(
        self,
        execution_id: str,
        message_sender: MessageSender,
    ):
        """
        初始化日志流式传输器

        Args:
            execution_id: 任务执行 ID
            message_sender: 消息发送器（实现 send_message 方法）
        """
        self.execution_id = execution_id
        self._message_sender = message_sender
        self._enabled = False
        self._enabled_lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        """是否已开启实时推送"""
        return self._enabled

    async def enable(self) -> bool:
        """
        开启实时推送模式

        Returns:
            是否成功开启
        """
        async with self._enabled_lock:
            if self._enabled:
                logger.debug(f"[{self.execution_id}] 实时推送已开启，跳过")
                return True

            self._enabled = True
            logger.info(f"[{self.execution_id}] 开启实时推送模式")

        return True

    async def disable(self) -> bool:
        """
        关闭实时推送模式

        Returns:
            是否成功关闭
        """
        async with self._enabled_lock:
            if not self._enabled:
                logger.debug(f"[{self.execution_id}] 实时推送已关闭，跳过")
                return True

            self._enabled = False
            logger.info(f"[{self.execution_id}] 关闭实时推送模式")

        return True

    async def push(self, log_type: str, content: str) -> bool:
        """
        推送实时日志内容

        仅在实时模式开启时推送。

        Args:
            log_type: 日志类型 (stdout/stderr)
            content: 日志内容

        Returns:
            是否发送成功
        """
        if not self._enabled:
            return False

        if not content:
            return True

        return await self._send_realtime_message(log_type, content)

    async def _send_realtime_message(self, log_type: str, content: str) -> bool:
        """
        发送实时日志消息

        Args:
            log_type: 日志类型
            content: 日志内容

        Returns:
            是否发送成功
        """
        try:
            # 构建消息（使用通用格式，具体 protobuf 类型由调用方决定）
            message = {
                "type": "log_realtime",
                "execution_id": self.execution_id,
                "log_type": log_type,
                "content": content,
                "timestamp": int(time.time() * 1000),
            }

            return await self._message_sender.send_message(message)

        except Exception as e:
            logger.error(f"[{self.execution_id}] 发送实时日志消息失败: {e}")
            return False

    def get_status(self) -> dict:
        """
        获取传输器状态

        Returns:
            状态信息字典
        """
        return {
            "execution_id": self.execution_id,
            "enabled": self._enabled,
        }


class BufferedLogStreamer(LogStreamer):
    """
    带缓冲的日志流式传输器

    在基础流式传输器上增加缓冲功能，减少网络请求次数。
    """

    def __init__(
        self,
        execution_id: str,
        message_sender: MessageSender,
        buffer_size: int = 100,
        flush_interval: float = 0.5,
    ):
        """
        初始化带缓冲的日志流式传输器

        Args:
            execution_id: 任务执行 ID
            message_sender: 消息发送器
            buffer_size: 缓冲区大小（行数）
            flush_interval: 刷新间隔（秒）
        """
        super().__init__(execution_id, message_sender)
        self._buffer_size = buffer_size
        self._flush_interval = flush_interval
        self._stdout_buffer: list[str] = []
        self._stderr_buffer: list[str] = []
        self._buffer_lock = asyncio.Lock()
        self._flush_task: asyncio.Task | None = None

    async def enable(self) -> bool:
        """开启实时推送模式"""
        result = await super().enable()
        if result and self._flush_task is None:
            self._flush_task = asyncio.create_task(self._flush_loop())
        return result

    async def disable(self) -> bool:
        """关闭实时推送模式"""
        # 先刷新缓冲区
        await self._flush_buffers()

        # 停止刷新任务
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._flush_task
            self._flush_task = None

        return await super().disable()

    async def push(self, log_type: str, content: str) -> bool:
        """
        推送日志到缓冲区

        Args:
            log_type: 日志类型 (stdout/stderr)
            content: 日志内容

        Returns:
            是否成功添加到缓冲区
        """
        if not self._enabled:
            return False

        if not content:
            return True

        async with self._buffer_lock:
            if log_type == "stdout":
                self._stdout_buffer.append(content)
                if len(self._stdout_buffer) >= self._buffer_size:
                    await self._flush_stdout()
            else:
                self._stderr_buffer.append(content)
                if len(self._stderr_buffer) >= self._buffer_size:
                    await self._flush_stderr()

        return True

    async def _flush_loop(self) -> None:
        """定期刷新缓冲区"""
        while self._enabled:
            try:
                await asyncio.sleep(self._flush_interval)
                if not self._enabled:
                    break
                await self._flush_buffers()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[{self.execution_id}] 刷新缓冲区异常: {e}")

    async def _flush_buffers(self) -> None:
        """刷新所有缓冲区"""
        async with self._buffer_lock:
            await self._flush_stdout()
            await self._flush_stderr()

    async def _flush_stdout(self) -> None:
        """刷新 stdout 缓冲区"""
        if self._stdout_buffer:
            content = "\n".join(self._stdout_buffer)
            self._stdout_buffer.clear()
            await self._send_realtime_message("stdout", content)

    async def _flush_stderr(self) -> None:
        """刷新 stderr 缓冲区"""
        if self._stderr_buffer:
            content = "\n".join(self._stderr_buffer)
            self._stderr_buffer.clear()
            await self._send_realtime_message("stderr", content)

    def get_status(self) -> dict:
        """获取传输器状态"""
        status = super().get_status()
        status.update(
            {
                "buffer_size": self._buffer_size,
                "flush_interval": self._flush_interval,
                "stdout_buffered": len(self._stdout_buffer),
                "stderr_buffered": len(self._stderr_buffer),
            }
        )
        return status
