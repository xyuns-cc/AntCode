"""
Redis 日志流服务

从 Redis Streams 订阅日志并推送到 WebSocket。
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from antcode_core.infrastructure.redis.client import get_redis_client
from antcode_web_api.websockets.websocket_connection_manager import websocket_manager


@dataclass
class StreamFollower:
    execution_id: str
    last_id: str = "0-0"
    ref_count: int = 0
    running: bool = False
    task: asyncio.Task | None = None
    history_sent: bool = False


class RedisLogStreamService:
    """Redis Stream 日志订阅服务"""

    def __init__(
        self,
        namespace: str = "antcode",
        batch_size: int = 200,
        block_ms: int = 5000,
    ):
        self._namespace = namespace
        self._batch_size = batch_size
        self._block_ms = block_ms
        self._followers: dict[str, StreamFollower] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, execution_id: str) -> None:
        """订阅执行日志"""
        async with self._lock:
            follower = self._followers.get(execution_id)
            if follower:
                follower.ref_count += 1
                return

            follower = StreamFollower(execution_id=execution_id, ref_count=1)
            self._followers[execution_id] = follower

        await self._start_follower(follower)

    async def unsubscribe(self, execution_id: str) -> None:
        """取消订阅执行日志"""
        async with self._lock:
            follower = self._followers.get(execution_id)
            if not follower:
                return

            follower.ref_count -= 1
            if follower.ref_count > 0:
                return

            self._followers.pop(execution_id, None)

        await self._stop_follower(follower)

    def _stream_key(self, execution_id: str) -> str:
        return f"{self._namespace}:log:stream:{execution_id}"

    async def _start_follower(self, follower: StreamFollower) -> None:
        follower.running = True

        if not follower.history_sent:
            last_id, sent = await self._send_history(follower.execution_id)
            follower.last_id = last_id
            follower.history_sent = True
            if sent == 0:
                await websocket_manager.send_no_historical_logs(follower.execution_id)
            else:
                await websocket_manager.send_historical_logs_end(
                    follower.execution_id, sent
                )

        follower.task = asyncio.create_task(self._follow_stream(follower))

    async def _stop_follower(self, follower: StreamFollower) -> None:
        follower.running = False
        if follower.task:
            follower.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await follower.task
            follower.task = None

    async def _send_history(self, execution_id: str) -> tuple[str, int]:
        """发送历史日志"""
        await websocket_manager.send_historical_logs_start(execution_id)

        last_id = "0-0"
        sent = 0
        redis = await get_redis_client()
        stream_key = self._stream_key(execution_id)

        # 先从 Redis Stream 读取
        while True:
            result = await redis.xread({stream_key: last_id}, count=self._batch_size)
            if not result:
                break

            _, messages = result[0]
            if not messages:
                break

            for msg_id, fields in messages:
                last_id = self._decode_value(msg_id)
                log_entry = self._decode_log(fields)
                await self._emit_log(execution_id, log_entry, source="history")
                sent += 1

        # 如果 Redis 没有数据，尝试从 S3 读取
        if sent == 0:
            sent = await self._send_history_from_s3(execution_id)

        return last_id, sent

    async def _send_history_from_s3(self, execution_id: str) -> int:
        """从 S3 读取历史日志（支持压缩文件）"""
        import gzip

        import aiohttp

        sent = 0
        try:
            from antcode_core.infrastructure.storage.log_storage import get_log_storage

            log_storage = get_log_storage()

            for log_type in ["stdout", "stderr"]:
                try:
                    # 先尝试获取预签名下载 URL（压缩文件）
                    url = await log_storage.get_presigned_download_url(execution_id, log_type)
                    if url:
                        async with (
                            aiohttp.ClientSession() as session,
                            session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp,
                        ):
                            if resp.status == 200:
                                data = await resp.read()
                                try:
                                    content = gzip.decompress(data).decode("utf-8")
                                except Exception:
                                    content = data.decode("utf-8", errors="ignore")

                                # 按行发送
                                for i, line in enumerate(content.split("\n")):
                                    if line.strip():
                                        log_entry = {
                                            "log_type": log_type,
                                            "content": line,
                                            "timestamp": "",
                                            "sequence": str(i),
                                        }
                                        await self._emit_log(execution_id, log_entry, source="s3_history")
                                        sent += 1
                        continue

                    # 回退到 query_logs（JSONL 格式）
                    query_result = await log_storage.query_logs(
                        run_id=execution_id,
                        log_type=log_type,
                        limit=10000,
                    )

                    for entry in query_result.entries:
                        log_entry = {
                            "log_type": entry.log_type,
                            "content": entry.content,
                            "timestamp": entry.timestamp.isoformat() if entry.timestamp else "",
                            "sequence": str(entry.sequence),
                        }
                        await self._emit_log(execution_id, log_entry, source="s3_history")
                        sent += 1

                except Exception as e:
                    logger.debug(f"从 S3 读取 {log_type} 日志失败: {e}")

        except Exception as e:
            logger.debug(f"S3 日志存储不可用: {e}")

        return sent

    async def _follow_stream(self, follower: StreamFollower) -> None:
        """持续跟随日志流"""
        redis = await get_redis_client()
        stream_key = self._stream_key(follower.execution_id)
        last_id = follower.last_id

        while follower.running:
            try:
                result = await redis.xread(
                    {stream_key: last_id},
                    count=self._batch_size,
                    block=self._block_ms,
                )
                if not result:
                    continue

                _, messages = result[0]
                for msg_id, fields in messages:
                    last_id = self._decode_value(msg_id)
                    log_entry = self._decode_log(fields)
                    await self._emit_log(follower.execution_id, log_entry, source="realtime")

                follower.last_id = last_id

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"日志流读取失败: {e}")
                await asyncio.sleep(1.0)

    async def _emit_log(self, execution_id: str, log_entry: dict[str, Any], source: str) -> None:
        log_type = log_entry.get("log_type") or "stdout"
        content = log_entry.get("content") or ""
        timestamp = log_entry.get("timestamp") or datetime.now(UTC).isoformat()
        level = "ERROR" if log_type == "stderr" else "INFO"

        message = {
            "type": "log_line",
            "execution_id": execution_id,
            "data": {
                "execution_id": execution_id,
                "log_type": log_type,
                "content": content,
                "timestamp": timestamp,
                "level": level,
                "source": source,
            },
            "timestamp": datetime.now(UTC).isoformat(),
        }
        await websocket_manager.broadcast_to_execution(execution_id, message)

    def _decode_log(self, fields: dict[str, Any]) -> dict[str, Any]:
        # Redis 返回的 fields 键可能是 bytes 或 str，需要兼容处理
        def get_field(name: str) -> Any:
            return fields.get(name) or fields.get(name.encode("utf-8"))

        return {
            "log_type": self._decode_value(get_field("log_type")),
            "content": self._decode_value(get_field("content")),
            "timestamp": self._decode_value(get_field("timestamp")),
            "sequence": self._decode_value(get_field("sequence")),
        }

    def _decode_value(self, value: Any) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value) if value is not None else ""


redis_log_stream_service = RedisLogStreamService()

__all__ = ["RedisLogStreamService", "redis_log_stream_service"]
