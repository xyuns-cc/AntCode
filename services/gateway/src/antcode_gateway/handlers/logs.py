"""
日志处理器

接收 Worker 通过 ``DataService.StreamLogs`` 上报的 ``LogBatch``，
以 **Proto bytes** 单字段框架（``{PROTO_FIELD: bytes}``）写入 Redis Stream，
由 Master ``LogIngestLoop`` 用 ``ProtoCodec(data_pb2.LogBatch)`` 解码。

P1c 改造：彻底移除 JSON 落 Stream 路径，统一走 Proto bytes，端到端与 P1a Master 对齐。
SendLog / SendLogBatch / SendLogChunk 三套 RPC 合并为 ``StreamLogs`` 单一路径。

**Validates: Requirements 6.6**

存储策略：
- 实时日志 -> Redis Streams（Proto bytes，供 SSE 推送与 Master 摄取）
- 持久化 -> master log_ingest_loop 消费 Redis 落 PG（``task_logs`` 表）
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from antcode_contracts import data_pb2
from antcode_core.application.services.workers.log_batch_validation import validate_log_batch
from antcode_core.application.services.workers.log_ingest_fence import append_fenced_log_batch
from antcode_core.common.log_limits import LogBatchLimits
from antcode_core.infrastructure.redis import decode_stream_payload, log_stream_key
from antcode_core.infrastructure.redis.control_plane import log_ingest_stream_key
from antcode_core.infrastructure.redis.stream_client import ProtoCodec, StreamClient
from loguru import logger

from antcode_gateway.config import gateway_config

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


def _positive_limit(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} 必须是正整数")
    return value


class LogHandler:
    """日志处理器

    接受 ``LogBatch`` Proto 消息，按 ``run_id`` 拆分后写入对应日志 Stream，
    每条 Stream 消息即一个 ``LogBatch`` Proto 的序列化字节（单字段 'p'）。
    """

    def __init__(
        self,
        redis_client=None,
        stream: StreamClient | None = None,
        *,
        max_batch_bytes: int | None = None,
        max_entry_content_bytes: int | None = None,
    ):
        """初始化处理器

        Args:
            redis_client: Redis 客户端（用于 pipeline 写 expire 等低级操作）
            stream: 注入测试用的 ``StreamClient``；默认创建带
                ``ProtoCodec(LogBatch)`` 的实例
        """
        self._redis_client = redis_client
        self._max_batch_bytes = _positive_limit(
            "max_batch_bytes",
            gateway_config.log_max_batch_bytes if max_batch_bytes is None else max_batch_bytes,
        )
        self._max_entry_content_bytes = _positive_limit(
            "max_entry_content_bytes",
            gateway_config.log_max_entry_content_bytes if max_entry_content_bytes is None else max_entry_content_bytes,
        )
        # ProtoCodec 仅用于 xadd_typed/xreadgroup_typed；下面 pipeline 路径绕过它
        self._stream = stream or StreamClient(codec=ProtoCodec(data_pb2.LogBatch))

    def _stream_key(self, run_id: str | None = None) -> str:
        """全局 ingest stream key（与 Master ingest loop 默认订阅对齐）。

        ``run_id`` 参数保留只是为了兼容老调用点签名；所有 ``xadd`` 都打到
        单一 ingest stream，Master 解码 LogBatch 后按 ``entry.run_id`` 路由。
        旧 per-run stream 已废弃。
        """
        return log_ingest_stream_key()

    def _legacy_per_run_stream_key(self, run_id: str) -> str:
        """旧 per-run stream key（仅 ``get_logs`` / ``cleanup_logs`` 兼容路径用）。"""
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

    # =========================================================================
    # Proto 入口 - StreamLogs / 内部测试都从这里进
    # =========================================================================

    async def handle_log_batch(self, batch: data_pb2.LogBatch) -> bool:
        """以 Proto bytes 单字段框架写入全局 ingest stream。

        改造点（与 Master ingest loop 对齐）：
        - 不再按 run_id 拆 sub-stream，所有 ``LogBatch`` 整体 xadd 到
          ``<namespace>:log:ingest``；
        - ``LogBatch`` 内部已自带 ``run_id`` 字段，Master 解码后按
          ``entry.run_id`` 路由；
        - 单一 stream 让 Master 单 consumer group 全量消费，避免 per-run
          stream key 的水平扩散。
        """
        self._validate_batch_bytes(batch)
        if not batch.entries:
            return True

        redis = await self._get_redis_client()
        if redis is None:
            # P1-02: Redis 不可用时**必须** fail-closed 返回 False,让 worker
            # StreamLogs 端拿到 StatusAck=failed,保留发送端 outbox + 原任务 PEL,
            # 下一轮重试。之前 warning 后 return True 是"发送端认为成功、服务端尚未持久化"→
            # 日志永久丢失。
            logger.error("Redis 不可用,拒绝确认日志接收(fail-closed,worker 保留 outbox)")
            return False

        try:
            await append_fenced_log_batch(
                redis,
                batch.SerializeToString(),
                worker_id=batch.worker_id,
                lease_id=batch.lease_id,
                run_ids={entry.run_id for entry in batch.entries},
            )
        except Exception as exc:
            logger.exception(f"写入日志 ingest stream 失败: {exc}")
            raise

        # 日志经 Redis ingest stream 由 master log_ingest_loop 消费落 PG,
        # gateway 端不再做副持久化(旧 log_storage 模块已随重构下线)。
        return True

    def _validate_batch_bytes(self, batch: data_pb2.LogBatch) -> None:
        validate_log_batch(
            batch,
            limits=LogBatchLimits(
                max_batch_bytes=self._max_batch_bytes,
                max_entry_content_bytes=self._max_entry_content_bytes,
            ),
        )

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
            # 兼容查询：旧 per-run stream 仍有可能持有未到期日志
            stream_key = self._legacy_per_run_stream_key(run_id)
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
        """清理某 run_id 的旧 per-run stream（ingest stream 是全局共享的,
        不能按 run_id 删除,由 MAXLEN/TTL 自动 trim）。"""
        redis = await self._get_redis_client()
        if redis is None:
            return False
        try:
            stream_key = self._legacy_per_run_stream_key(run_id)
            await redis.delete(stream_key)
            logger.debug(f"日志 Stream 已清理（legacy per-run）: {stream_key}")
            return True
        except Exception as exc:
            logger.exception(f"清理日志 Stream 失败: {exc}")
            return False
