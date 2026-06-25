"""
日志处理器

接收 Worker 通过 ``DataService.StreamLogs`` 上报的 ``LogBatch``，
以 **Proto bytes** 单字段框架（``{PROTO_FIELD: bytes}``）写入 Redis Stream，
由 Master ``LogIngestLoop`` 用 ``ProtoCodec(data_pb2.LogBatch)`` 解码。

P1c 改造：彻底移除 JSON 落 Stream 路径，统一走 Proto bytes，端到端与 P1a Master 对齐。
SendLog / SendLogBatch / SendLogChunk 三套 RPC 合并为 ``StreamLogs`` 单一路径。

**Validates: Requirements 6.6**

存储策略：
1. 实时日志 -> Redis Streams（Proto bytes，供 WebSocket 推送 & Master 摄取）
2. 持久化 -> log_storage（按需，落 S3 / ClickHouse / FS）
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import TYPE_CHECKING

from antcode_contracts import data_pb2
from antcode_core.common.config import settings
from antcode_core.infrastructure.redis import decode_stream_payload, log_stream_key
from antcode_core.infrastructure.redis.stream_client import ProtoCodec, StreamClient
from loguru import logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    pass


def _log_type_from_proto(log_type: int) -> str:
    """``data_pb2.LogType`` enum -> 兼容旧 log_storage 的字符串"""
    if log_type == data_pb2.LOG_TYPE_STDERR:
        return "stderr"
    if log_type == data_pb2.LOG_TYPE_SYSTEM:
        return "system"
    return "stdout"


def _entry_timestamp_seconds(entry: data_pb2.LogEntry) -> float:
    if not entry.HasField("timestamp"):
        return time.time()
    ts = entry.timestamp
    return ts.seconds + ts.nanos / 1e9


class LogHandler:
    """日志处理器

    接受 ``LogBatch`` Proto 消息，按 ``run_id`` 拆分后写入对应日志 Stream，
    每条 Stream 消息即一个 ``LogBatch`` Proto 的序列化字节（单字段 'p'）。
    """

    MAX_STREAM_LENGTH = settings.LOG_STREAM_MAXLEN
    STREAM_TTL_SECONDS = settings.LOG_STREAM_TTL_SECONDS

    def __init__(
        self,
        redis_client=None,
        log_storage=None,
        stream: StreamClient | None = None,
    ):
        """初始化处理器

        Args:
            redis_client: Redis 客户端（用于 pipeline 写 expire 等低级操作）
            log_storage: 日志持久化存储后端
            stream: 注入测试用的 ``StreamClient``；默认创建带
                ``ProtoCodec(LogBatch)`` 的实例
        """
        self._redis_client = redis_client
        self._log_storage = log_storage
        # ProtoCodec 仅用于 xadd_typed/xreadgroup_typed；下面 pipeline 路径绕过它
        self._stream = stream or StreamClient(codec=ProtoCodec(data_pb2.LogBatch))

    def _stream_key(self, run_id: str) -> str:
        return log_stream_key(run_id)

    async def _get_redis_client(self):
        if self._redis_client is None:
            try:
                from antcode_core.infrastructure.redis import get_redis_client

                self._redis_client = await get_redis_client()
            except ImportError:
                logger.warning("antcode_core.infrastructure.redis 不可用")
                return None
        return self._redis_client

    async def _get_log_storage(self):
        if self._log_storage is None:
            try:
                from antcode_core.infrastructure.storage.log_storage import get_log_storage

                self._log_storage = get_log_storage()
            except ImportError:
                logger.warning("antcode_core.infrastructure.storage.log_storage 不可用")
                return None
        return self._log_storage

    # =========================================================================
    # Proto 入口 - StreamLogs / 内部测试都从这里进
    # =========================================================================

    async def handle_log_batch(self, batch: data_pb2.LogBatch) -> bool:
        """以 Proto bytes 单字段框架写入每个 run_id 对应的日志 Stream。

        ``LogBatch`` 内多个 entry 可能属于不同 ``run_id``，所以按 run_id 分桶后
        各自合并成一个 ``LogBatch`` 子消息再 xadd。这样 Master 那边解码出来
        仍然是完整的 ``LogBatch`` 结构。
        """
        if not batch.entries:
            return True

        # 按 run_id 分桶
        by_run: dict[str, list[data_pb2.LogEntry]] = {}
        for entry in batch.entries:
            by_run.setdefault(entry.run_id, []).append(entry)

        redis = await self._get_redis_client()
        if redis is None:
            logger.warning("Redis 不可用，跳过日志 Stream 写入")
        else:
            pipe = redis.pipeline(transaction=False)
            expire_keys: set[str] = set()
            from antcode_core.infrastructure.redis.stream_client import PROTO_FIELD

            for run_id, entries in by_run.items():
                stream_key = self._stream_key(run_id)
                sub_batch = data_pb2.LogBatch(
                    worker_id=batch.worker_id,
                    entries=entries,
                )
                if batch.HasField("trace"):
                    sub_batch.trace.CopyFrom(batch.trace)

                pipe.xadd(
                    stream_key,
                    {PROTO_FIELD: sub_batch.SerializeToString()},
                    maxlen=self.MAX_STREAM_LENGTH,
                    approximate=True,
                )
                if self.STREAM_TTL_SECONDS > 0:
                    expire_keys.add(stream_key)

            for stream_key in expire_keys:
                pipe.expire(stream_key, self.STREAM_TTL_SECONDS)

            try:
                await pipe.execute()
            except Exception as exc:
                logger.exception(f"写入日志 Stream 失败: {exc}")
                return False

        # 持久化（best-effort，不阻塞实时推送）
        await self._persist_log_batch(batch)
        return True

    async def _persist_log_batch(self, batch: data_pb2.LogBatch) -> None:
        log_storage = await self._get_log_storage()
        if log_storage is None:
            return

        try:
            from antcode_core.infrastructure.storage.log_storage import (
                LogEntry as StorageLogEntry,
            )
        except ImportError:
            return

        for entry in batch.entries:
            try:
                storage_entry = StorageLogEntry(
                    run_id=entry.run_id,
                    log_type=_log_type_from_proto(entry.log_type),
                    content=entry.content,
                    sequence=entry.sequence,
                    timestamp=datetime.fromtimestamp(_entry_timestamp_seconds(entry)),
                )
                result = await log_storage.write_log(storage_entry)
                if not getattr(result, "success", True):
                    logger.warning(f"持久化日志失败: {getattr(result, 'error', '')}")
            except Exception as exc:
                logger.exception(f"持久化日志条目失败: {exc}")

    # =========================================================================
    # 查询/清理 - 维持原接口，给 web_api / 调试用
    # =========================================================================

    async def get_logs(
        self,
        run_id: str,
        start_id: str = "0",
        count: int = 100,
    ) -> list[dict]:
        """从 Redis Stream 读取日志（解码 Proto LogBatch）。"""
        redis = await self._get_redis_client()
        if redis is None:
            return []

        try:
            stream_key = self._stream_key(run_id)
            messages = await redis.xrange(stream_key, min=start_id, max="+", count=count)

            logs: list[dict] = []
            for message_id, data in messages:
                mid = message_id.decode() if isinstance(message_id, bytes) else str(message_id)
                payload = self._decode_log_message(data)
                if payload is None:
                    continue
                for entry in payload.entries:
                    logs.append(
                        {
                            "id": mid,
                            "log_type": _log_type_from_proto(entry.log_type),
                            "content": entry.content,
                            "timestamp": _entry_timestamp_seconds(entry),
                            "sequence": int(entry.sequence),
                        }
                    )
            return logs
        except Exception as exc:
            logger.exception(f"读取日志失败: {exc}")
            return []

    def _decode_log_message(self, data: dict) -> data_pb2.LogBatch | None:
        """从 Redis Stream 字段 dict 中解码 LogBatch。

        Proto bytes 路径优先（兼容字节/字符串两种 key）；如果没有 'p' 字段，
        回落到旧 JSON 路径并尽力构造单 entry 的 LogBatch。
        """
        from antcode_core.infrastructure.redis.stream_client import PROTO_FIELD

        raw = data.get(PROTO_FIELD) or data.get("p")
        if raw is not None:
            try:
                if isinstance(raw, str):
                    raw = raw.encode("latin-1")  # bytes 透传
                batch = data_pb2.LogBatch()
                batch.ParseFromString(raw)
                return batch
            except Exception as exc:
                logger.exception(f"解码 LogBatch Proto 失败: {exc}")
                return None

        # 兼容历史 JSON 帧
        decoded = decode_stream_payload(data)
        if "content" not in decoded:
            return None
        log_type = decoded.get("log_type", "stdout")
        log_type_enum = data_pb2.LOG_TYPE_STDOUT
        if log_type == "stderr":
            log_type_enum = data_pb2.LOG_TYPE_STDERR
        elif log_type == "system":
            log_type_enum = data_pb2.LOG_TYPE_SYSTEM

        entry = data_pb2.LogEntry(
            run_id=str(decoded.get("run_id", "")),
            log_type=log_type_enum,
            content=str(decoded.get("content", "")),
            sequence=int(decoded.get("sequence", 0) or 0),
        )
        ts_value = decoded.get("timestamp")
        if ts_value:
            try:
                seconds = float(ts_value)
                entry.timestamp.seconds = int(seconds)
                entry.timestamp.nanos = int((seconds - int(seconds)) * 1e9)
            except (TypeError, ValueError):
                pass
        return data_pb2.LogBatch(entries=[entry])

    async def cleanup_logs(self, run_id: str) -> bool:
        redis = await self._get_redis_client()
        if redis is None:
            return False
        try:
            stream_key = self._stream_key(run_id)
            await redis.delete(stream_key)
            logger.debug(f"日志 Stream 已清理: {stream_key}")
            return True
        except Exception as exc:
            logger.exception(f"清理日志 Stream 失败: {exc}")
            return False
