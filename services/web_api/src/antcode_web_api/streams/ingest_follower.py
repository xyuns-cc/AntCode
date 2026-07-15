"""Redis ingest 日志跟随器（原 redis_log_stream_service 的 SSE 版）。

- 实时：所有 run 共享一个全局 ``<namespace>:log:ingest`` Stream 订阅协程
  （xread 从 ``$`` 只读新消息），解码 LogBatch 后按 ``entry.run_id`` 命中
  broker 已订阅集合的条目投递（fan-out）。
- 历史：主路径 PG ``task_logs``；PG 空时回落旧 per-run stream。与原实现
  不同，历史读取只返回条目列表，由 log_stream_service 编排发送——这样
  编排层能先注册队列再读快照，用 sequence 阈值过滤重叠，不重不漏。
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from antcode_core.infrastructure.redis import (
    decode_stream_payload,
    get_redis_client,
    log_stream_key,
    redis_namespace,
)
from loguru import logger

from antcode_web_api.streams.run_stream_broker import run_stream_broker
from antcode_web_api.streams.sse import build_log_line_message, normalize_sequence


def _log_ingest_stream_key(namespace: str | None = None) -> str:
    """全局日志摄取 Stream key（与 Gateway / Master 同名同效）。"""
    return f"{redis_namespace(namespace)}:log:ingest"


class IngestLogFollower:
    """全局 ingest stream 跟随器（refcount 启停）。"""

    def __init__(
        self,
        namespace: str | None = None,
        batch_size: int = 200,
        block_ms: int = 5000,
    ):
        self._namespace = redis_namespace(namespace)
        self._batch_size = batch_size
        self._block_ms = block_ms
        self._follow_counts: dict[str, int] = {}
        self._lock = asyncio.Lock()
        self._ingest_task: asyncio.Task | None = None
        self._ingest_running = False
        self._ingest_last_id = "$"  # 只关心新消息；历史走 PG

    async def follow(self, run_id: str) -> None:
        """开始跟随执行日志（多订阅者 ref-count）。"""
        async with self._lock:
            self._follow_counts[run_id] = self._follow_counts.get(run_id, 0) + 1
        await self._ensure_ingest_task()

    async def unfollow(self, run_id: str) -> None:
        async with self._lock:
            count = self._follow_counts.get(run_id, 0) - 1
            if count > 0:
                self._follow_counts[run_id] = count
            else:
                self._follow_counts.pop(run_id, None)
            still_following = bool(self._follow_counts)

        if not still_following:
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

    # ------------------------------------------------------------------ #
    # 历史读取（返回条目列表，由编排层发送）
    # ------------------------------------------------------------------ #

    async def fetch_history(self, run_id: str, limit: int = 10000) -> list[dict[str, Any]]:
        """历史日志：主路径 PG，PG 空时回落旧 per-run stream。

        返回归一化条目 ``{log_type, content, timestamp, sequence(int|None), source}``。
        """
        entries: list[dict[str, Any]] = []

        try:
            from antcode_core.application.services.logs.postgres_log_service import (
                postgres_log_service,
            )

            pg_entries = await postgres_log_service.list_entries(run_id, limit=limit)
            for entry in pg_entries:
                ts = ""
                if entry.timestamp:
                    try:
                        ts = entry.timestamp.isoformat()
                    except Exception:
                        ts = str(entry.timestamp)
                entries.append(
                    {
                        "log_type": entry.log_type or "stdout",
                        "content": entry.content or "",
                        "timestamp": ts,
                        "sequence": normalize_sequence(entry.sequence),
                        "source": "pg_history",
                    }
                )
        except Exception as e:
            logger.debug("PG 历史日志读取失败 run_id={}: {}", run_id, e)

        if entries:
            return entries

        try:
            return await self._fetch_history_from_per_run_stream(run_id)
        except Exception as e:
            logger.debug("per-run stream 历史读取失败 run_id={}: {}", run_id, e)
            return []

    async def _fetch_history_from_per_run_stream(self, run_id: str) -> list[dict[str, Any]]:
        """旧 per-run stream 兼容路径（ingest pipeline 还没把日志刷到 PG 时）。"""
        redis = await get_redis_client()
        if redis is None:
            return []
        stream_key = log_stream_key(run_id, namespace=self._namespace)
        last_id = "0-0"
        entries: list[dict[str, Any]] = []
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
                    log_entry["source"] = "legacy_history"
                    entries.append(log_entry)
        return entries

    # ------------------------------------------------------------------ #
    # 实时跟随
    # ------------------------------------------------------------------ #

    async def _ingest_loop(self) -> None:
        """全局 ingest stream 订阅协程（所有订阅者共享）。"""
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
                    subscribed = run_stream_broker.subscribed_runs()
                    if not subscribed:
                        continue
                    by_run = self._decode_batch_grouped(fields, subscribed)
                    for run_id, entries in by_run.items():
                        for log_entry in entries:
                            run_stream_broker.publish(
                                run_id,
                                build_log_line_message(
                                    run_id,
                                    log_type=log_entry.get("log_type") or "stdout",
                                    content=log_entry.get("content") or "",
                                    timestamp=log_entry.get("timestamp") or None,
                                    sequence=log_entry.get("sequence"),
                                    source="realtime",
                                ),
                            )

                self._ingest_last_id = last_id

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"ingest stream 读取失败: {e}")
                await asyncio.sleep(1.0)

        self._ingest_running = False

    # ------------------------------------------------------------------ #
    # 解码工具
    # ------------------------------------------------------------------ #

    def _decode_batch(self, fields: dict[Any, Any], run_id_filter: str | None = None) -> list[dict[str, Any]]:
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
                    out.append(self._proto_entry_to_dict(entry))
                return out
            except Exception as e:
                logger.debug("解码 LogBatch Proto 失败: {}", e)

        # 旧 JSON 路径
        decoded = decode_stream_payload(fields)
        msg_run_id = decoded.get("run_id") or ""
        if run_id_filter and msg_run_id and msg_run_id != run_id_filter:
            return []
        return [self._json_entry_to_dict(decoded)]

    def _decode_batch_grouped(self, fields: dict[Any, Any], subscribed: set[str]) -> dict[str, list[dict[str, Any]]]:
        """ingest stream 专用：批量解码并按 ``run_id`` 分组，仅保留命中 ``subscribed`` 的。"""
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
                    grouped[entry.run_id].append(self._proto_entry_to_dict(entry))
                return grouped
            except Exception as e:
                logger.debug("ingest stream 解码 LogBatch 失败: {}", e)

        # 兼容旧 JSON
        decoded = decode_stream_payload(fields)
        run_id = decoded.get("run_id") or ""
        if run_id in subscribed:
            grouped[run_id].append(self._json_entry_to_dict(decoded))
        return grouped

    def _proto_entry_to_dict(self, entry: Any) -> dict[str, Any]:
        from antcode_contracts import data_pb2

        name = data_pb2.LogType.Name(entry.log_type)
        log_type = name.removeprefix("LOG_TYPE_").lower() if name.startswith("LOG_TYPE_") else name.lower()
        ts = ""
        if entry.HasField("timestamp"):
            seconds = entry.timestamp.seconds + entry.timestamp.nanos / 1e9
            try:
                ts = datetime.fromtimestamp(seconds, tz=UTC).isoformat()
            except Exception:
                ts = ""
        return {
            "log_type": log_type,
            "content": entry.content or "",
            "timestamp": ts,
            "sequence": normalize_sequence(entry.sequence),
        }

    def _json_entry_to_dict(self, decoded: dict[Any, Any]) -> dict[str, Any]:
        return {
            "log_type": self._decode_value(decoded.get("log_type")) or "stdout",
            "content": self._decode_value(decoded.get("content")),
            "timestamp": self._decode_value(decoded.get("timestamp")),
            "sequence": normalize_sequence(self._decode_value(decoded.get("sequence"))),
        }

    def _decode_value(self, value: Any) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value) if value is not None else ""


ingest_log_follower = IngestLogFollower()

__all__ = ["IngestLogFollower", "ingest_log_follower"]
