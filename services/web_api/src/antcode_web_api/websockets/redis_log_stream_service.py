"""
Redis 日志流服务

P2 改造：
- 历史日志改读 PG ``task_logs`` (``postgres_log_service.list_entries``)。
- 实时流改订阅全局 ``<namespace>:log:ingest``，解码 LogBatch 后按
  ``entry.run_id`` 路由给已订阅的 WebSocket 客户端。
- 多客户端订阅同一 ``run_id`` 共享同一 ingest 消费协程；多个 ``run_id``
  共享同一个全局 ingest 订阅协程（fan-out）。
- 旧 per-run stream / S3 仅作为 PG 暂未刷库时的回落。
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from antcode_core.infrastructure.redis import (
    decode_stream_payload,
    get_redis_client,
    log_stream_key,
    redis_namespace,
)
from loguru import logger

from antcode_web_api.websockets.websocket_connection_manager import websocket_manager


def _log_ingest_stream_key(namespace: str | None = None) -> str:
    """全局日志摄取 Stream key。

    与 Gateway / Master 同名同效，本地定义避免跨包修改 ``control_plane.py``。
    路径：``<namespace>:log:ingest``。
    """
    return f"{redis_namespace(namespace)}:log:ingest"


@dataclass
class StreamFollower:
    run_id: str
    ref_count: int = 0
    history_sent: bool = False


class RedisLogStreamService:
    """Redis Stream 日志订阅服务

    架构（P2）：
    - 所有 ``run_id`` 共享同一个 ingest 订阅协程 ``_ingest_task``；
    - ``_subscribed_runs`` 维护当前正在被前端 WS 订阅的 ``run_id`` 集合；
    - ingest 协程读到 batch 后,把 ``entry.run_id`` 命中集合的条目分发出去；
    - 历史回放走 PG ``postgres_log_service.list_entries``。
    """

    def __init__(
        self,
        namespace: str | None = None,
        batch_size: int = 200,
        block_ms: int = 5000,
    ):
        self._namespace = redis_namespace(namespace)
        self._batch_size = batch_size
        self._block_ms = block_ms
        self._followers: dict[str, StreamFollower] = {}
        self._subscribed_runs: set[str] = set()
        self._lock = asyncio.Lock()
        self._ingest_task: asyncio.Task | None = None
        self._ingest_running = False
        self._ingest_last_id = "$"  # 只关心新消息；历史走 PG

    async def subscribe(self, run_id: str) -> None:
        """订阅执行日志（多客户端 ref-count）"""
        async with self._lock:
            follower = self._followers.get(run_id)
            if follower:
                follower.ref_count += 1
            else:
                follower = StreamFollower(run_id=run_id, ref_count=1)
                self._followers[run_id] = follower
            self._subscribed_runs.add(run_id)
            need_history = not follower.history_sent

        if need_history:
            await self._send_history(run_id)
            async with self._lock:
                f = self._followers.get(run_id)
                if f:
                    f.history_sent = True

        await self._ensure_ingest_task()

    async def unsubscribe(self, run_id: str) -> None:
        """取消订阅执行日志"""
        async with self._lock:
            follower = self._followers.get(run_id)
            if not follower:
                return

            follower.ref_count -= 1
            if follower.ref_count > 0:
                return

            self._followers.pop(run_id, None)
            self._subscribed_runs.discard(run_id)
            still_have_followers = bool(self._followers)

        if not still_have_followers:
            await self._stop_ingest_task()

    async def _ensure_ingest_task(self) -> None:
        async with self._lock:
            if self._ingest_running and self._ingest_task and not self._ingest_task.done():
                return
            self._ingest_running = True
            self._ingest_task = asyncio.create_task(self._ingest_loop())

    async def _stop_ingest_task(self) -> None:
        async with self._lock:
            self._ingest_running = False
            task = self._ingest_task
            self._ingest_task = None
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _send_history(self, run_id: str) -> None:
        """历史回放：主路径 PG, 回落 per-run stream + S3"""
        await websocket_manager.send_historical_logs_start(run_id)
        sent = 0

        # 1. PG 历史（主路径）
        try:
            from antcode_core.application.services.logs.postgres_log_service import (
                postgres_log_service,
            )

            entries = await postgres_log_service.list_entries(run_id, limit=10000)
            for entry in entries:
                ts = ""
                if entry.timestamp:
                    try:
                        ts = entry.timestamp.isoformat()
                    except Exception:
                        ts = str(entry.timestamp)
                log_entry = {
                    "log_type": entry.log_type or "stdout",
                    "content": entry.content or "",
                    "timestamp": ts,
                    "sequence": str(entry.sequence),
                }
                await self._emit_log(run_id, log_entry, source="pg_history")
                sent += 1
        except Exception as e:
            logger.debug("PG 历史日志读取失败 run_id={}: {}", run_id, e)

        # 2. 回落：旧 per-run stream（ingest pipeline 还没把日志刷到 PG 时）
        if sent == 0:
            try:
                sent += await self._send_history_from_per_run_stream(run_id)
            except Exception as e:
                logger.debug("per-run stream 历史读取失败 run_id={}: {}", run_id, e)

        # 3. 终极回落：S3
        if sent == 0:
            try:
                sent += await self._send_history_from_s3(run_id)
            except Exception as e:
                logger.debug("S3 历史读取失败 run_id={}: {}", run_id, e)

        if sent == 0:
            await websocket_manager.send_no_historical_logs(run_id)
        else:
            await websocket_manager.send_historical_logs_end(run_id, sent)

    async def _send_history_from_per_run_stream(self, run_id: str) -> int:
        """旧 per-run stream 兼容路径。"""
        redis = await get_redis_client()
        if redis is None:
            return 0
        stream_key = log_stream_key(run_id, namespace=self._namespace)
        last_id = "0-0"
        sent = 0
        while True:
            result = await redis.xread({stream_key: last_id}, count=self._batch_size)
            if not result:
                break
            _, messages = result[0]
            if not messages:
                break
            for msg_id, fields in messages:
                last_id = self._decode_value(msg_id)
                for log_entry in self._decode_batch(fields, run_id_filter=run_id):
                    await self._emit_log(run_id, log_entry, source="legacy_history")
                    sent += 1
        return sent

    async def _send_history_from_s3(self, run_id: str) -> int:
        """从 S3 读取历史日志（支持压缩文件）"""
        import gzip

        import aiohttp

        sent = 0
        try:
            from antcode_core.infrastructure.storage.log_storage import get_log_storage

            log_storage = get_log_storage()

            for log_type in ["stdout", "stderr"]:
                try:
                    url = await log_storage.get_presigned_download_url(run_id, log_type)
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

                                for i, line in enumerate(content.split("\n")):
                                    if line.strip():
                                        log_entry = {
                                            "log_type": log_type,
                                            "content": line,
                                            "timestamp": "",
                                            "sequence": str(i),
                                        }
                                        await self._emit_log(run_id, log_entry, source="s3_history")
                                        sent += 1
                        continue

                    query_result = await log_storage.query_logs(
                        run_id=run_id,
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
                        await self._emit_log(run_id, log_entry, source="s3_history")
                        sent += 1

                except Exception as e:
                    logger.debug(f"从 S3 读取 {log_type} 日志失败: {e}")

        except Exception as e:
            logger.debug(f"S3 日志存储不可用: {e}")

        return sent

    async def _ingest_loop(self) -> None:
        """全局 ingest stream 订阅协程（所有订阅者共享）。

        只读 ``$`` 之后的新消息（历史走 PG），解码 LogBatch 后按
        ``entry.run_id`` 命中 ``_subscribed_runs`` 时分发。
        """
        redis = await get_redis_client()
        if redis is None:
            logger.warning("Redis 不可用，跳过 ingest stream 订阅")
            self._ingest_running = False
            return

        stream_key = _log_ingest_stream_key(self._namespace)
        last_id = self._ingest_last_id

        while self._ingest_running:
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
                    # 复制订阅集合，避免分发期间被修改
                    subscribed = set(self._subscribed_runs)
                    if not subscribed:
                        continue
                    by_run = self._decode_batch_grouped(fields, subscribed)
                    for run_id, entries in by_run.items():
                        for log_entry in entries:
                            await self._emit_log(run_id, log_entry, source="realtime")

                self._ingest_last_id = last_id

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"ingest stream 读取失败: {e}")
                await asyncio.sleep(1.0)

        self._ingest_running = False

    async def _emit_log(self, run_id: str, log_entry: dict[str, Any], source: str) -> None:
        log_type = log_entry.get("log_type") or "stdout"
        content = log_entry.get("content") or ""
        timestamp = log_entry.get("timestamp") or datetime.now(UTC).isoformat()
        level = "ERROR" if log_type == "stderr" else "INFO"

        message = {
            "type": "log_line",
            "run_id": run_id,
            "data": {
                "run_id": run_id,
                "log_type": log_type,
                "content": content,
                "timestamp": timestamp,
                "level": level,
                "source": source,
            },
            "timestamp": datetime.now(UTC).isoformat(),
        }
        await websocket_manager.broadcast_to_run(run_id, message)

    # ------------------------------------------------------------------ #
    # 解码工具
    # ------------------------------------------------------------------ #

    def _decode_batch(
        self, fields: dict[Any, Any], run_id_filter: str | None = None
    ) -> list[dict[str, Any]]:
        """解码一个 stream 消息 -> entry 列表。支持 Proto LogBatch 和旧 JSON。"""
        proto_raw = fields.get(b"p") or fields.get("p")
        if proto_raw is not None:
            try:
                from antcode_contracts import data_pb2

                if isinstance(proto_raw, str):
                    proto_raw = proto_raw.encode("latin-1")
                batch = data_pb2.LogBatch()
                batch.ParseFromString(proto_raw)
                out: list[dict[str, Any]] = []
                for entry in batch.entries:
                    if run_id_filter and entry.run_id != run_id_filter:
                        continue
                    name = data_pb2.LogType.Name(entry.log_type)
                    log_type = (
                        name.removeprefix("LOG_TYPE_").lower()
                        if name.startswith("LOG_TYPE_")
                        else name.lower()
                    )
                    ts = ""
                    if entry.HasField("timestamp"):
                        seconds = entry.timestamp.seconds + entry.timestamp.nanos / 1e9
                        try:
                            ts = datetime.fromtimestamp(seconds, tz=UTC).isoformat()
                        except Exception:
                            ts = ""
                    out.append(
                        {
                            "log_type": log_type,
                            "content": entry.content or "",
                            "timestamp": ts,
                            "sequence": str(entry.sequence),
                        }
                    )
                return out
            except Exception as e:
                logger.debug("解码 LogBatch Proto 失败: {}", e)

        # 旧 JSON 路径
        decoded = decode_stream_payload(fields)
        msg_run_id = decoded.get("run_id") or ""
        if run_id_filter and msg_run_id and msg_run_id != run_id_filter:
            return []
        return [
            {
                "log_type": self._decode_value(decoded.get("log_type")) or "stdout",
                "content": self._decode_value(decoded.get("content")),
                "timestamp": self._decode_value(decoded.get("timestamp")),
                "sequence": self._decode_value(decoded.get("sequence")),
            }
        ]

    def _decode_batch_grouped(
        self, fields: dict[Any, Any], subscribed: set[str]
    ) -> dict[str, list[dict[str, Any]]]:
        """ingest stream 专用：批量解码并按 ``run_id`` 分组,仅保留命中 ``subscribed`` 的。"""
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)

        proto_raw = fields.get(b"p") or fields.get("p")
        if proto_raw is not None:
            try:
                from antcode_contracts import data_pb2

                if isinstance(proto_raw, str):
                    proto_raw = proto_raw.encode("latin-1")
                batch = data_pb2.LogBatch()
                batch.ParseFromString(proto_raw)
                for entry in batch.entries:
                    if entry.run_id not in subscribed:
                        continue
                    name = data_pb2.LogType.Name(entry.log_type)
                    log_type = (
                        name.removeprefix("LOG_TYPE_").lower()
                        if name.startswith("LOG_TYPE_")
                        else name.lower()
                    )
                    ts = ""
                    if entry.HasField("timestamp"):
                        seconds = entry.timestamp.seconds + entry.timestamp.nanos / 1e9
                        try:
                            ts = datetime.fromtimestamp(seconds, tz=UTC).isoformat()
                        except Exception:
                            ts = ""
                    grouped[entry.run_id].append(
                        {
                            "log_type": log_type,
                            "content": entry.content or "",
                            "timestamp": ts,
                            "sequence": str(entry.sequence),
                        }
                    )
                return grouped
            except Exception as e:
                logger.debug("ingest stream 解码 LogBatch 失败: {}", e)

        # 兼容旧 JSON
        decoded = decode_stream_payload(fields)
        run_id = decoded.get("run_id") or ""
        if run_id in subscribed:
            grouped[run_id].append(
                {
                    "log_type": self._decode_value(decoded.get("log_type")) or "stdout",
                    "content": self._decode_value(decoded.get("content")),
                    "timestamp": self._decode_value(decoded.get("timestamp")),
                    "sequence": self._decode_value(decoded.get("sequence")),
                }
            )
        return grouped

    def _decode_value(self, value: Any) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value) if value is not None else ""


redis_log_stream_service = RedisLogStreamService()

__all__ = ["RedisLogStreamService", "redis_log_stream_service"]
