"""Database-authoritative realtime event follower for Web API SSE clients.

- Realtime: each Web API process follows ``{<namespace>}:log:sse-events`` and
  fans persisted ``log_line`` and ``run_status`` events out to local clients.
- History: ``IngestLogHistoryReader`` reads PostgreSQL, with legacy per-run
  streams retained only as a historical compatibility source.

The raw ``log:ingest`` stream is deliberately not read here. Master publishes
realtime log events only after PostgreSQL persistence assigns authoritative
sequences, preventing uncommitted lines and ingest-retention races from being
exposed to clients.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from antcode_core.infrastructure.redis import get_redis_client, redis_namespace
from loguru import logger

from antcode_web_api.streams.ingest_cursor import initial_stream_cursors
from antcode_web_api.streams.ingest_dead_letter import isolate_bad_ingest_frame
from antcode_web_api.streams.ingest_decoder import decode_value
from antcode_web_api.streams.ingest_history import IngestLogHistoryReader
from antcode_web_api.streams.run_stream_broker import run_stream_broker
from antcode_web_api.streams.sse_event_stream import decode_sse_event, sse_event_stream_key

EVENT_RETRY_DELAY_SECONDS = 1.0
REALTIME_EVENT_TYPES = frozenset({"log_line", "run_status"})


@dataclass(frozen=True)
class _EventReadContext:
    redis: Any
    event_key: str
    cursors: dict[str, str]


@dataclass(frozen=True)
class _EventFrame:
    stream_key: str
    message_id: str
    fields: dict[Any, Any]


class IngestLogFollower:
    """由应用生命周期常驻运行、按活跃 run 引用分发的 SSE event follower。"""

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
        self._ready_event: asyncio.Event | None = None
        self._startup_error: Exception | None = None
        self._last_error: str | None = None
        self._resume_cursors: dict[str, str] = {}
        self._history_reader = IngestLogHistoryReader(self._namespace)

    async def follow(self, run_id: str) -> None:
        """开始跟随执行日志（多订阅者 ref-count）。"""
        async with self._lock:
            self._follow_counts[run_id] = self._follow_counts.get(run_id, 0) + 1
        try:
            await self._ensure_ingest_task()
        except BaseException:
            await self._release_follow_reference(run_id)
            await self._stop_ingest_task()
            raise

    async def unfollow(self, run_id: str) -> None:
        await self._release_follow_reference(run_id)

    async def _release_follow_reference(self, run_id: str) -> None:
        """释放一次跟随引用；生命周期 follower 不随零订阅停止。"""
        async with self._lock:
            count = self._follow_counts.get(run_id, 0) - 1
            if count > 0:
                self._follow_counts[run_id] = count
            else:
                self._follow_counts.pop(run_id, None)

    async def start(self) -> None:
        """应用 lifespan 启动常驻 reader，失败时阻断启动。"""
        try:
            await self._ensure_ingest_task()
        except BaseException:
            await self._stop_ingest_task()
            raise

    async def _ensure_ingest_task(self) -> None:
        async with self._lock:
            if self._ingest_running and self._ingest_task and not self._ingest_task.done():
                ready_event = self._ready_event
            else:
                self._ingest_running = True
                self._startup_error = None
                self._ready_event = asyncio.Event()
                ready_event = self._ready_event
                self._ingest_task = asyncio.create_task(self._ingest_loop())
        if ready_event is None:
            raise RuntimeError("ingest follower 初始化事件缺失")
        await ready_event.wait()
        if self._startup_error is not None:
            raise RuntimeError("ingest follower 初始化失败") from self._startup_error

    async def _stop_ingest_task(self) -> None:
        async with self._lock:
            self._ingest_running = False
            task = self._ingest_task
            self._ingest_task = None
        if task:
            task.cancel()
            results = await asyncio.gather(task, return_exceptions=True)
            error = results[0]
            if isinstance(error, Exception) and not isinstance(error, asyncio.CancelledError):
                logger.error("停止 ingest follower 时任务异常: {}", error)

    async def shutdown(self) -> None:
        """应用关闭时停止 follower，不受订阅计数影响。"""
        async with self._lock:
            self._follow_counts.clear()
        await self._stop_ingest_task()

    def healthy(self) -> bool:
        """生命周期 follower 必须已就绪、持续存活且没有读取错误。"""
        if self._last_error is not None:
            return False
        task = self._ingest_task
        ready = self._ready_event
        return bool(task and not task.done() and ready and ready.is_set() and self._last_error is None)

    @property
    def history_reader(self) -> IngestLogHistoryReader:
        return self._history_reader

    async def _ingest_loop(self) -> None:
        """全局 SSE event stream 订阅协程（所有订阅者共享）。"""
        this_task = asyncio.current_task()
        try:
            context = await self._initialize_read_context()
        except asyncio.CancelledError:
            self._signal_ready()
            self._set_not_running(this_task)
            return
        except Exception as exc:
            self._startup_error = exc
            self._last_error = str(exc)
            logger.exception("ingest follower 初始化失败: {}", exc)
            self._signal_ready()
            self._set_not_running(this_task)
            return

        self._last_error = None
        self._signal_ready()

        while self._ingest_running:
            try:
                await self._read_once(context)
                self._last_error = None
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._last_error = str(e)
                logger.exception("SSE event stream 读取失败: {}", e)
                await asyncio.sleep(EVENT_RETRY_DELAY_SECONDS)

        self._set_not_running(this_task)

    async def _initialize_read_context(self) -> _EventReadContext:
        redis = await get_redis_client()
        if redis is None:
            raise RuntimeError("Redis 客户端不可用")
        event_key = sse_event_stream_key(self._namespace)
        cursors = await initial_stream_cursors(
            redis,
            event_key=event_key,
            resume_cursors=self._resume_cursors,
        )
        return _EventReadContext(redis=redis, event_key=event_key, cursors=cursors)

    async def _read_once(self, context: _EventReadContext) -> None:
        result = await context.redis.xread(
            context.cursors,
            count=self._batch_size,
            block=self._block_ms,
        )
        for raw_stream_key, messages in result or []:
            stream_key = decode_value(raw_stream_key)
            for message_id, fields in messages:
                frame = _EventFrame(stream_key, decode_value(message_id), fields)
                await self._process_frame(context, frame)

    async def _process_frame(self, context: _EventReadContext, frame: _EventFrame) -> None:
        try:
            if frame.stream_key != context.event_key:
                raise ValueError(f"SSE follower 收到非事件流帧: {frame.stream_key}")
            self._publish_message(frame.fields)
        except Exception as exc:
            await isolate_bad_ingest_frame(
                context.redis,
                namespace=self._namespace,
                source_stream=frame.stream_key,
                message_id=frame.message_id,
                fields=frame.fields,
                error=exc,
            )
            logger.error(
                "SSE event 坏帧已隔离: stream={} msg_id={} err={}",
                frame.stream_key,
                frame.message_id,
                exc,
            )
        if frame.stream_key == context.event_key:
            context.cursors[context.event_key] = frame.message_id
            self._resume_cursors[context.event_key] = frame.message_id

    def _signal_ready(self) -> None:
        if self._ready_event is not None:
            self._ready_event.set()

    def _publish_message(self, fields: dict[Any, Any]) -> None:
        subscribed = run_stream_broker.subscribed_runs()
        if not subscribed:
            return
        message = decode_sse_event(fields)
        event_type = message.get("type")
        run_id = message.get("run_id")
        if event_type not in REALTIME_EVENT_TYPES:
            raise ValueError(f"不支持的 SSE 实时事件类型: {event_type!r}")
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("SSE 实时事件缺少有效 run_id")
        if not isinstance(message.get("data"), dict):
            raise ValueError("SSE 实时事件缺少 data 对象")
        if run_id in subscribed:
            run_stream_broker.publish(run_id, message)

    def _set_not_running(self, this_task: asyncio.Task | None) -> None:
        """退出路径只允许"当前在任"的任务清运行标志。

        被 cancel 的旧任务收尾时，_ensure_ingest_task 可能已为新订阅者创建
        接替任务并置位 _ingest_running——旧任务无条件写 False 会扼杀接替任务
        （其 while 循环首轮即退出，订阅者只收 ping 收不到实时帧）。
        """
        if self._ingest_task is this_task or self._ingest_task is None:
            self._ingest_running = False


ingest_log_follower = IngestLogFollower()

__all__ = ["IngestLogFollower", "ingest_log_follower"]
