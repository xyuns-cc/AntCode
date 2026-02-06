"""
日志归档器

负责将本地日志文件分片传输到 Gateway/Redis 进行持久化存储。
支持 ACK 确认和断点续传。
"""

import asyncio
import contextlib
import hashlib
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

import aiofiles
from loguru import logger


def _get_env_int(name: str, default: int) -> int:
    """从环境变量获取整数"""
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except ValueError:
        return default


def _get_env_float(name: str, default: float) -> float:
    """从环境变量获取浮点数"""
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return float(value)
    except ValueError:
        return default


class TransferState(Enum):
    """传输状态"""

    INIT = "init"
    STREAMING = "streaming"
    WAIT_ACK = "wait_ack"
    RETRYING = "retrying"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    ERROR = "error"


class MessageSender(Protocol):
    """消息发送器协议"""

    async def send_message(self, message: Any) -> bool:
        """发送消息到 Gateway/Redis"""
        ...


@dataclass
class ArchiverConfig:
    """归档器配置"""

    chunk_size: int = 131072  # 分片大小（字节），默认 128KB
    chunk_interval: float = 1.0  # 分片检查间隔（秒）
    max_in_flight: int = 8  # 最大在途分片数
    ack_timeout: float = 5.0  # ACK 超时（秒）
    retry_base: float = 0.5  # 重试基准延迟（秒）
    retry_max_delay: float = 5.0  # 最大重试延迟（秒）
    retry_max: int = 5  # 最大重试次数
    max_rate: int = 800 * 1024  # 最大传输速率（字节/秒）

    @classmethod
    def from_env(cls) -> "ArchiverConfig":
        """从环境变量构建配置"""
        return cls(
            chunk_size=_get_env_int("LOG_CHUNK_SIZE", cls.chunk_size),
            chunk_interval=_get_env_float("LOG_CHUNK_INTERVAL", cls.chunk_interval),
            max_in_flight=_get_env_int("LOG_MAX_IN_FLIGHT", cls.max_in_flight),
            ack_timeout=_get_env_float("LOG_ACK_TIMEOUT", cls.ack_timeout),
            retry_base=_get_env_float("LOG_RETRY_BASE", cls.retry_base),
            retry_max_delay=_get_env_float("LOG_RETRY_MAX_DELAY", cls.retry_max_delay),
            retry_max=_get_env_int("LOG_RETRY_MAX", cls.retry_max),
            max_rate=_get_env_int("LOG_WORKER_MAX_RATE", cls.max_rate),
        )


@dataclass
class InFlightChunk:
    """在途分片信息"""

    offset: int
    size: int
    sent_at: float
    retry_count: int = 0
    checksum: str = ""


@dataclass
class TransferMeta:
    """传输元数据"""

    execution_id: str
    log_type: str
    last_acked_offset: int = 0
    last_sent_offset: int = 0
    total_size: int = -1
    completed: bool = False


class LogArchiver:
    """
    日志归档器

    将本地日志文件分片传输到 Gateway/Redis。

    功能：
    - 分片读取和发送
    - ACK 确认和重试
    - 断点续传
    - 速率限制
    """

    def __init__(
        self,
        execution_id: str,
        log_type: str,
        log_file: Path | str,
        message_sender: MessageSender,
        config: ArchiverConfig | None = None,
        on_state_change: Callable[[TransferState, TransferState], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ):
        """
        初始化日志归档器

        Args:
            execution_id: 任务执行 ID
            log_type: 日志类型 (stdout/stderr)
            log_file: 本地日志文件路径
            message_sender: 消息发送器
            config: 归档配置
            on_state_change: 状态变更回调
            on_error: 错误回调
        """
        self.execution_id = execution_id
        self.log_type = log_type
        self.log_file = Path(log_file)
        self._message_sender = message_sender
        self._config = config or ArchiverConfig()
        self._on_state_change = on_state_change
        self._on_error = on_error

        # 状态管理
        self._state = TransferState.INIT
        self._state_lock = asyncio.Lock()

        # 偏移量管理
        self._last_acked_offset: int = 0
        self._last_sent_offset: int = 0
        self._file_size: int = 0

        # 在途分片管理
        self._in_flight_chunks: dict[int, InFlightChunk] = {}
        self._in_flight_lock = asyncio.Lock()

        # 任务控制
        self._running = False
        self._send_task: asyncio.Task | None = None
        self._ack_check_task: asyncio.Task | None = None

        # 完成标志
        self._finalize_requested = False
        self._total_size: int = -1

        # 统计
        self._bytes_sent: int = 0
        self._send_start_time: float | None = None

    @property
    def state(self) -> TransferState:
        """当前状态"""
        return self._state

    @property
    def last_acked_offset(self) -> int:
        """已确认的最大偏移量"""
        return self._last_acked_offset

    @property
    def in_flight_count(self) -> int:
        """在途分片数"""
        return len(self._in_flight_chunks)

    @property
    def is_completed(self) -> bool:
        """是否传输完成"""
        return self._state == TransferState.COMPLETED

    async def _set_state(self, new_state: TransferState) -> None:
        """设置状态"""
        async with self._state_lock:
            old_state = self._state
            if old_state == new_state:
                return

            self._state = new_state
            logger.debug(
                f"[{self.execution_id}/{self.log_type}] 状态变更: "
                f"{old_state.value} -> {new_state.value}"
            )

            if self._on_state_change:
                try:
                    self._on_state_change(old_state, new_state)
                except Exception as e:
                    logger.error(f"状态变更回调异常: {e}")

    async def start(self) -> None:
        """启动分片传输"""
        if self._running:
            logger.warning(f"[{self.execution_id}/{self.log_type}] 归档器已在运行")
            return

        self._running = True
        await self._set_state(TransferState.STREAMING)

        self._send_task = asyncio.create_task(self._send_loop())
        self._ack_check_task = asyncio.create_task(self._ack_check_loop())

        logger.info(
            f"[{self.execution_id}/{self.log_type}] 归档器已启动, "
            f"从 offset={self._last_acked_offset} 开始"
        )

    async def stop(self) -> None:
        """停止分片传输"""
        self._running = False

        if self._send_task and not self._send_task.done():
            self._send_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._send_task

        if self._ack_check_task and not self._ack_check_task.done():
            self._ack_check_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._ack_check_task

        logger.info(f"[{self.execution_id}/{self.log_type}] 归档器已停止")

    async def finalize(self) -> None:
        """请求完成传输"""
        self._finalize_requested = True
        self._total_size = (
            self.log_file.stat().st_size if self.log_file.exists() else 0
        )
        await self._set_state(TransferState.FINALIZING)
        logger.info(f"[{self.execution_id}/{self.log_type}] 请求完成传输")

    async def handle_ack(
        self, ack_offset: int, ok: bool, error: str = ""
    ) -> None:
        """
        处理来自服务端的 ACK

        Args:
            ack_offset: 已确认的偏移量
            ok: 是否成功
            error: 错误信息
        """
        if not ok:
            logger.warning(
                f"[{self.execution_id}/{self.log_type}] ACK 失败: "
                f"offset={ack_offset}, error={error}"
            )
            return

        async with self._in_flight_lock:
            # 移除已确认的分片
            to_remove = [
                offset
                for offset, chunk in self._in_flight_chunks.items()
                if offset + chunk.size <= ack_offset
            ]
            for offset in to_remove:
                del self._in_flight_chunks[offset]

        # 更新已确认偏移量
        if ack_offset > self._last_acked_offset:
            self._last_acked_offset = ack_offset

        # 检查是否完成
        if (
            self._finalize_requested
            and self._last_acked_offset >= self._total_size
            and self.in_flight_count == 0
        ):
            await self._set_state(TransferState.COMPLETED)
            logger.info(
                f"[{self.execution_id}/{self.log_type}] 传输完成, "
                f"total={self._total_size} bytes"
            )

    async def _send_loop(self) -> None:
        """分片发送循环"""
        try:
            while self._running:
                if self._state in (TransferState.COMPLETED, TransferState.ERROR):
                    break

                await self._try_send_chunks()
                await asyncio.sleep(self._config.chunk_interval)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[{self.execution_id}/{self.log_type}] 发送循环异常: {e}")
            await self._set_state(TransferState.ERROR)
            if self._on_error:
                self._on_error(str(e))

    async def _try_send_chunks(self) -> None:
        """尝试发送分片"""
        if self.in_flight_count >= self._config.max_in_flight:
            return

        if not self.log_file.exists():
            return

        self._file_size = self.log_file.stat().st_size

        if self._last_sent_offset >= self._file_size:
            if self._finalize_requested and self.in_flight_count == 0:
                await self._send_final_chunk()
            return

        available_slots = self._config.max_in_flight - self.in_flight_count

        for _ in range(available_slots):
            if self._last_sent_offset >= self._file_size:
                break
            await self._send_next_chunk()

    async def _send_next_chunk(self) -> None:
        """发送下一个分片"""
        offset = self._last_sent_offset
        chunk_size = min(self._config.chunk_size, self._file_size - offset)

        if chunk_size <= 0:
            return

        try:
            async with aiofiles.open(self.log_file, "rb") as f:
                await f.seek(offset)
                chunk_data = await f.read(chunk_size)
        except Exception as e:
            logger.error(f"[{self.execution_id}/{self.log_type}] 读取分片失败: {e}")
            return

        checksum = hashlib.sha256(chunk_data).hexdigest()[:16]

        success = await self._send_chunk(
            chunk=chunk_data,
            offset=offset,
            is_final=False,
            checksum=checksum,
        )

        if success:
            async with self._in_flight_lock:
                self._in_flight_chunks[offset] = InFlightChunk(
                    offset=offset,
                    size=len(chunk_data),
                    sent_at=time.time(),
                    checksum=checksum,
                )

            self._last_sent_offset = offset + len(chunk_data)
            self._bytes_sent += len(chunk_data)

            if self._send_start_time is None:
                self._send_start_time = time.time()

            logger.debug(
                f"[{self.execution_id}/{self.log_type}] 发送分片: "
                f"offset={offset}, size={len(chunk_data)}"
            )

    async def _send_final_chunk(self) -> None:
        """发送最终分片"""
        success = await self._send_chunk(
            chunk=b"",
            offset=self._total_size,
            is_final=True,
            total_size=self._total_size,
        )

        if success:
            logger.info(
                f"[{self.execution_id}/{self.log_type}] 发送最终分片, "
                f"total_size={self._total_size}"
            )

    async def _send_chunk(
        self,
        chunk: bytes,
        offset: int,
        is_final: bool,
        checksum: str = "",
        total_size: int = -1,
    ) -> bool:
        """发送分片消息"""
        try:
            message = {
                "type": "log_chunk",
                "execution_id": self.execution_id,
                "log_type": self.log_type,
                "offset": offset,
                "chunk": chunk,
                "checksum": checksum,
                "is_final": is_final,
                "total_size": total_size if is_final else -1,
            }

            return await self._message_sender.send_message(message)

        except Exception as e:
            logger.error(f"[{self.execution_id}/{self.log_type}] 发送分片失败: {e}")
            return False

    async def _ack_check_loop(self) -> None:
        """ACK 超时检查循环"""
        while self._running:
            try:
                await asyncio.sleep(self._config.ack_timeout / 2)
                if not self._running:
                    break

                await self._check_ack_timeout()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(
                    f"[{self.execution_id}/{self.log_type}] ACK 检查异常: {e}"
                )

    async def _check_ack_timeout(self) -> None:
        """检查 ACK 超时"""
        now = time.time()
        timeout_chunks = []

        async with self._in_flight_lock:
            for offset, chunk in self._in_flight_chunks.items():
                if now - chunk.sent_at > self._config.ack_timeout:
                    timeout_chunks.append((offset, chunk))

        for offset, chunk in timeout_chunks:
            if chunk.retry_count >= self._config.retry_max:
                logger.error(
                    f"[{self.execution_id}/{self.log_type}] 分片重试次数超限: "
                    f"offset={offset}"
                )
                await self._set_state(TransferState.ERROR)
                if self._on_error:
                    self._on_error(f"分片 {offset} 重试次数超限")
                return

            # 重试发送
            await self._retry_chunk(offset, chunk)

    async def _retry_chunk(self, offset: int, chunk: InFlightChunk) -> None:
        """重试发送分片"""
        try:
            async with aiofiles.open(self.log_file, "rb") as f:
                await f.seek(offset)
                chunk_data = await f.read(chunk.size)

            success = await self._send_chunk(
                chunk=chunk_data,
                offset=offset,
                is_final=False,
                checksum=chunk.checksum,
            )

            if success:
                async with self._in_flight_lock:
                    if offset in self._in_flight_chunks:
                        self._in_flight_chunks[offset].sent_at = time.time()
                        self._in_flight_chunks[offset].retry_count += 1

                logger.debug(
                    f"[{self.execution_id}/{self.log_type}] 重试分片: "
                    f"offset={offset}, retry={chunk.retry_count + 1}"
                )

        except Exception as e:
            logger.error(
                f"[{self.execution_id}/{self.log_type}] 重试分片失败: {e}"
            )

    async def wait_for_completion(self, timeout: float = 60.0) -> bool:
        """
        等待传输完成

        Args:
            timeout: 超时时间（秒）

        Returns:
            是否成功完成
        """
        start = time.time()
        while time.time() - start < timeout:
            if self._state == TransferState.COMPLETED:
                return True
            if self._state == TransferState.ERROR:
                return False
            await asyncio.sleep(0.5)
        return False

    def get_status(self) -> dict:
        """获取归档器状态"""
        return {
            "execution_id": self.execution_id,
            "log_type": self.log_type,
            "state": self._state.value,
            "last_acked_offset": self._last_acked_offset,
            "last_sent_offset": self._last_sent_offset,
            "file_size": self._file_size,
            "in_flight_count": self.in_flight_count,
            "bytes_sent": self._bytes_sent,
            "finalize_requested": self._finalize_requested,
        }
