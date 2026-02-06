"""
日志处理器

接收 Worker 的实时日志流，写入 Redis Streams 和持久化存储。

**Validates: Requirements 6.6**

存储策略：
1. 实时日志 -> Redis Streams（用于 WebSocket 推送）
2. 持久化 -> S3/ClickHouse（通过 log_storage 模块）
"""

import time
from dataclasses import dataclass, field
from datetime import datetime

from loguru import logger

from antcode_core.common.config import settings


@dataclass
class LogEntry:
    """日志条目"""

    run_id: str
    log_type: str = "stdout"  # stdout, stderr
    content: str = ""
    timestamp: float = field(default_factory=time.time)
    sequence: int = 0


@dataclass
class LogChunk:
    """日志分片"""

    run_id: str
    log_type: str = "stdout"
    chunk: bytes = b""
    offset: int = 0
    is_final: bool = False
    checksum: str = ""
    total_size: int = -1


class LogHandler:
    """日志处理器

    接收 Worker 的日志流：
    1. 实时日志写入 Redis Streams（用于 WebSocket 推送）
    2. 日志分片写入持久化存储（S3/ClickHouse）
    """

    # Redis Streams 键前缀
    LOG_STREAM_PREFIX = f"{settings.REDIS_NAMESPACE}:log:stream:"
    MAX_STREAM_LENGTH = settings.LOG_STREAM_MAXLEN
    STREAM_TTL_SECONDS = settings.LOG_STREAM_TTL_SECONDS

    def __init__(self, redis_client=None, log_storage=None):
        """初始化处理器

        Args:
            redis_client: Redis 客户端，默认延迟初始化
            log_storage: 日志持久化存储后端，默认延迟初始化
        """
        self._redis_client = redis_client
        self._log_storage = log_storage

    async def _get_redis_client(self):
        """获取 Redis 客户端"""
        if self._redis_client is None:
            try:
                from antcode_core.infrastructure.redis import get_redis_client

                self._redis_client = await get_redis_client()
            except ImportError:
                logger.warning("antcode_core.infrastructure.redis 不可用")
                return None
        return self._redis_client

    async def _get_log_storage(self):
        """获取日志持久化存储后端"""
        if self._log_storage is None:
            try:
                from antcode_core.infrastructure.storage.log_storage import get_log_storage

                self._log_storage = get_log_storage()
            except ImportError:
                logger.warning("antcode_core.infrastructure.storage.log_storage 不可用")
                return None
        return self._log_storage

    async def handle_realtime_log(self, entry: LogEntry) -> bool:
        """处理实时日志

        将日志写入 Redis Streams（实时推送）和持久化存储。

        Args:
            entry: 日志条目

        Returns:
            是否成功
        """
        run_id = entry.run_id

        logger.debug(
            f"收到实时日志: run_id={run_id}, "
            f"type={entry.log_type}, size={len(entry.content)}"
        )

        # 1. 写入 Redis Stream（实时推送）
        redis = await self._get_redis_client()
        if redis is not None:
            try:
                stream_key = f"{self.LOG_STREAM_PREFIX}{run_id}"

                await redis.xadd(
                    stream_key,
                    {
                        "log_type": entry.log_type,
                        "content": entry.content,
                        "timestamp": str(entry.timestamp),
                        "sequence": str(entry.sequence),
                    },
                    maxlen=self.MAX_STREAM_LENGTH,
                    approximate=True,
                )
                if self.STREAM_TTL_SECONDS > 0:
                    await redis.expire(stream_key, self.STREAM_TTL_SECONDS)

                logger.debug(f"日志已写入 Stream: {stream_key}")

            except Exception as e:
                logger.error(f"写入日志 Stream 失败: {e}")

        # 2. 写入持久化存储（S3/ClickHouse）
        log_storage = await self._get_log_storage()
        if log_storage is not None:
            try:
                from antcode_core.infrastructure.storage.log_storage import LogEntry as StorageLogEntry

                storage_entry = StorageLogEntry(
                    run_id=run_id,
                    log_type=entry.log_type,
                    content=entry.content,
                    sequence=entry.sequence,
                    timestamp=datetime.fromtimestamp(entry.timestamp) if entry.timestamp else None,
                )
                result = await log_storage.write_log(storage_entry)

                if not result.success:
                    logger.warning(f"持久化日志失败: {result.error}")

            except Exception as e:
                logger.error(f"持久化日志失败: {e}")

        return True

    async def handle_log_chunk(self, chunk: LogChunk) -> dict:
        """处理日志分片

        将日志分片写入持久化存储（S3/ClickHouse）。

        Args:
            chunk: 日志分片

        Returns:
            ACK 结果 {"ok": bool, "ack_offset": int, "error": str}
        """
        run_id = chunk.run_id

        logger.debug(
            f"收到日志分片: run_id={run_id}, "
            f"type={chunk.log_type}, offset={chunk.offset}, "
            f"size={len(chunk.chunk)}, is_final={chunk.is_final}"
        )

        try:
            log_storage = await self._get_log_storage()

            if log_storage is None:
                logger.debug("log_storage 不可用，使用简单 ACK")
                return {
                    "ok": True,
                    "ack_offset": chunk.offset + len(chunk.chunk),
                    "error": "",
                }

            from antcode_core.infrastructure.storage.log_storage import LogChunk as StorageLogChunk

            storage_chunk = StorageLogChunk(
                run_id=run_id,
                log_type=chunk.log_type,
                data=chunk.chunk,
                offset=chunk.offset,
                is_final=chunk.is_final,
                checksum=chunk.checksum,
                total_size=chunk.total_size,
            )

            result = await log_storage.write_chunk(storage_chunk)

            # 如果是最后一个分片，触发合并
            if chunk.is_final and result.success:
                finalize_result = await log_storage.finalize_chunks(
                    run_id=run_id,
                    log_type=chunk.log_type,
                    total_size=chunk.total_size,
                    checksum=chunk.checksum,
                )
                if finalize_result.success:
                    logger.info(f"日志归档完成: {finalize_result.storage_path}")
                else:
                    logger.warning(f"日志归档失败: {finalize_result.error}")

            return {
                "ok": result.success,
                "ack_offset": result.ack_offset,
                "error": result.error or "",
            }

        except Exception as e:
            logger.error(f"处理日志分片失败: {e}")
            return {
                "ok": False,
                "ack_offset": chunk.offset,
                "error": str(e),
            }

    async def get_logs(
        self,
        run_id: str,
        start_id: str = "0",
        count: int = 100,
    ) -> list:
        """获取日志

        从 Redis Stream 读取日志。

        Args:
            run_id: 运行 ID
            start_id: 起始消息 ID
            count: 最大返回数量

        Returns:
            日志列表
        """
        redis = await self._get_redis_client()
        if redis is None:
            return []

        try:
            stream_key = f"{self.LOG_STREAM_PREFIX}{run_id}"

            # 使用 XRANGE 读取日志
            messages = await redis.xrange(
                stream_key,
                min=start_id,
                max="+",
                count=count,
            )

            logs = []
            for message_id, data in messages:
                # 解码数据
                decoded = {}
                for k, v in data.items():
                    key = k.decode() if isinstance(k, bytes) else k
                    value = v.decode() if isinstance(v, bytes) else v
                    decoded[key] = value

                logs.append({
                    "id": message_id.decode()
                    if isinstance(message_id, bytes)
                    else message_id,
                    "log_type": decoded.get("log_type", "stdout"),
                    "content": decoded.get("content", ""),
                    "timestamp": float(decoded.get("timestamp", 0)),
                    "sequence": int(decoded.get("sequence", 0)),
                })

            return logs

        except Exception as e:
            logger.error(f"读取日志失败: {e}")
            return []

    async def cleanup_logs(self, run_id: str) -> bool:
        """清理日志 Stream

        任务完成后清理 Redis 中的日志 Stream。

        Args:
            run_id: 运行 ID

        Returns:
            是否成功
        """
        redis = await self._get_redis_client()
        if redis is None:
            return False

        try:
            stream_key = f"{self.LOG_STREAM_PREFIX}{run_id}"
            await redis.delete(stream_key)
            logger.debug(f"日志 Stream 已清理: {stream_key}")
            return True
        except Exception as e:
            logger.error(f"清理日志 Stream 失败: {e}")
            return False
