"""Deadline-driven live phase for one SSE log connection."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import aclosing
from dataclasses import dataclass
from typing import Any

from loguru import logger

from antcode_web_api.streams.log_stream_gap import GapScanProgress, LogGapReader
from antcode_web_api.streams.log_stream_guard import StreamGuard, StreamGuardViolation
from antcode_web_api.streams.log_stream_replay import (
    ReplayState,
    emit_gap_entry,
    format_live_message,
    stream_cursor_frame,
)
from antcode_web_api.streams.run_stream_broker import QUEUE_CAPACITY_UNAVAILABLE, QUEUE_MAXSIZE, QUEUE_OVERFLOW
from antcode_web_api.streams.sse import build_ping_message, build_stream_error_message, format_sse_event

TerminalStatusLoader = Callable[[str], Awaitable[dict[str, Any] | None]]
TERMINAL_STATUSES = frozenset({"success", "failed", "timeout", "cancelled", "skipped", "rejected"})

# P1-SSE-03: backlog 轮间排空 broker 的单次出队等待；仅用于区分"队列已空"与
# "仍有消息"（wait_for 超时 0 会在队列任务执行前直接取消），不构成可感知延迟。
GAP_BACKLOG_DRAIN_TIMEOUT_SECONDS = 0.001


class _StreamTerminalFrame(Exception):
    """携带最后一帧并终止 SSE 流（guard 违规 / broker 队列不可恢复）。"""

    def __init__(self, frame: bytes) -> None:
        super().__init__("SSE stream terminated")
        self.frame = frame


@dataclass(frozen=True)
class ActiveStreamConfig:
    ping_interval: float
    gap_check_interval: float
    # 实时帧持续到达或已见终态时，缺口扫描退避到该间隔，降低每连接稳态 PG 负载。
    gap_check_backoff_interval: float = 30.0


@dataclass(frozen=True)
class ActiveStreamDependencies:
    broker: Any
    gap_reader: LogGapReader
    terminal_status_loader: TerminalStatusLoader


@dataclass
class LiveStreamState:
    """存活阶段的可变状态（frozen 的 ``ActiveStreamContext`` 上赋值会杀死 SSE 生成器）。

    ``known_status`` 随实时 run_status 帧与 PG 终态兜底推进；
    ``realtime_since_gap_check`` 供缺口扫描间隔自适应退避；``gap_backlog_pending``
    表示上一轮缺口扫描因预算截断仍有积压，下一轮应立即执行而非等常规间隔。
    """

    known_status: str
    realtime_since_gap_check: bool = False
    gap_backlog_pending: bool = False


@dataclass(frozen=True)
class ActiveStreamContext:
    run_id: str
    subscription: Any
    replay_state: ReplayState
    guard: StreamGuard
    live_state: LiveStreamState


@dataclass
class _Deadlines:
    ping: float
    gap: float

    @classmethod
    def start(cls, now: float, config: ActiveStreamConfig) -> _Deadlines:
        return cls(
            ping=now + config.ping_interval,
            gap=now + config.gap_check_interval,
        )

    def wait_timeout(self, now: float, config: ActiveStreamConfig) -> float:
        return max(0.0, min(self.ping, self.gap) - now)


class ActiveLogStream:
    def __init__(self, dependencies: ActiveStreamDependencies, config: ActiveStreamConfig) -> None:
        self._dependencies = dependencies
        self._config = config

    async def frames(self, context: ActiveStreamContext) -> AsyncIterator[bytes]:
        deadlines = _Deadlines.start(_monotonic(), self._config)
        while True:
            error = await self._guard_error(context)
            if error is not None:
                yield error
                return
            if _monotonic() >= deadlines.gap:
                try:
                    async for frame in self._gap_pass_frames(context, deadlines):
                        deadlines.ping = _monotonic() + self._config.ping_interval
                        yield frame
                except _StreamTerminalFrame as exc:
                    yield exc.frame
                    return
                except Exception as exc:
                    logger.exception("SSE 存活连接 PG 缺口检查失败 run_id={}: {}", context.run_id, exc)
                    yield _error_frame("日志缺口检查失败，请重新连接", "gap_check_failed")
                    return
                continue
            queue_frame, terminal = await self._next_queue_frame(context, deadlines)
            if queue_frame is not None:
                yield queue_frame
            if terminal:
                return

    async def _gap_pass_frames(
        self,
        context: ActiveStreamContext,
        deadlines: _Deadlines,
    ) -> AsyncIterator[bytes]:
        """单轮缺口扫描 + deadline 推进 + backlog 轮间 broker 排空。"""
        async for frame in self._gap_frames(context):
            yield frame
        if not context.live_state.gap_backlog_pending:
            deadlines.gap = _monotonic() + self._next_gap_interval(context)
            return
        # P1-SSE-03: 预算截断后立即续扫，但两轮之间必须先排空 broker 队列——
        # 此前连续 backlog 轮次完全不消费 broker，队列只进不出，必然 overflow 断连。
        deadlines.gap = _monotonic()
        async for frame in self._drain_broker_backlog(context):
            yield frame

    async def _drain_broker_backlog(self, context: ActiveStreamContext) -> AsyncIterator[bytes]:
        """排空 broker 队列（上限取队列容量，保证单次排空有界）。"""
        for _ in range(QUEUE_MAXSIZE):
            try:
                message = await self._dependencies.broker.get_message(
                    context.subscription,
                    GAP_BACKLOG_DRAIN_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                return
            frame, terminal = self._process_queue_message(context, message)
            if terminal and frame is not None:
                raise _StreamTerminalFrame(frame)
            if frame is not None:
                yield frame

    async def _gap_frames(self, context: ActiveStreamContext) -> AsyncIterator[bytes]:
        state = context.replay_state
        previous_watermark = state.storage_watermark
        window = await self._dependencies.gap_reader.plan(context.run_id, state.storage_watermark)
        await self._raise_on_guard(context)
        progress = GapScanProgress()
        last_confirmed = previous_watermark
        entries = self._dependencies.gap_reader.iter_window(window, progress)
        async with aclosing(entries):
            async for entry in entries:
                await self._raise_on_guard(context)
                if not state.overlaps_entry(entry):
                    yield emit_gap_entry(context.run_id, entry, state)
                state.confirm_persisted(entry)
                last_confirmed = max(last_confirmed, int(entry.get("storage_id") or 0))
        await self._raise_on_guard(context)
        # P1-SSE-02/04: 行数或字节预算截断时，水位只推进到已确认行，剩余
        # 缺口由下一轮（立即调度）按 keyset 游标继续；未截断则缺口读尽，
        # 水位推进到本轮快照。
        new_watermark = last_confirmed if progress.truncated else window.snapshot_id
        state.advance_storage_watermark(new_watermark)
        if new_watermark > previous_watermark:
            yield stream_cursor_frame(new_watermark)
        context.live_state.gap_backlog_pending = progress.truncated
        if context.live_state.gap_backlog_pending:
            return
        # 终态兜底只是 UX 补偿：缺口回填已成功，此处失败不应终止健康流
        # （DB 故障由 guard 周期重校验独立 fail-closed），留待下轮重试。
        try:
            terminal_frame = await self._terminal_status_frame(context)
        except Exception as exc:
            logger.warning("SSE 终态兜底状态读取失败，下轮重试 run_id={}: {}", context.run_id, exc)
            terminal_frame = None
        if terminal_frame is not None:
            yield terminal_frame

    def _next_gap_interval(self, context: ActiveStreamContext) -> float:
        """自适应缺口扫描间隔：实时帧持续到达（broker 健康）或已见终态时退避到
        ``gap_check_backoff_interval``，否则保持基础间隔，保证漏发日志与终态
        兜底的及时性；首次扫描 deadline 不受影响，首屏回填不会被推迟。
        """
        state = context.live_state
        flowing = state.realtime_since_gap_check
        state.realtime_since_gap_check = False
        if flowing or state.known_status in TERMINAL_STATUSES:
            return max(self._config.gap_check_backoff_interval, self._config.gap_check_interval)
        return self._config.gap_check_interval

    async def _raise_on_guard(self, context: ActiveStreamContext) -> None:
        error = await self._guard_error(context)
        if error is not None:
            raise _StreamTerminalFrame(error)

    async def _next_queue_frame(
        self,
        context: ActiveStreamContext,
        deadlines: _Deadlines,
    ) -> tuple[bytes | None, bool]:
        now = _monotonic()
        timeout = deadlines.wait_timeout(now, self._config)
        try:
            message = await self._dependencies.broker.get_message(context.subscription, timeout)
        except TimeoutError:
            if _monotonic() >= deadlines.ping:
                frame = format_sse_event("ping", build_ping_message())
                deadlines.ping = _monotonic() + self._config.ping_interval
                return frame, False
            return None, False
        deadlines.ping = _monotonic() + self._config.ping_interval
        return self._process_queue_message(context, message)

    def _process_queue_message(
        self,
        context: ActiveStreamContext,
        message: Any,
    ) -> tuple[bytes | None, bool]:
        queue_error = _queue_error_frame(message)
        if queue_error is not None:
            return queue_error, True
        if context.replay_state.overlaps(message):
            # C4：陈旧重复重投不代表 broker 有新数据，不能翻转"broker 健康"标志，
            # 否则缺口扫描误退避到 30s（漏发行应 5s 内被 PG 兜底）。仅新鲜帧置位。
            return None, False
        context.live_state.realtime_since_gap_check = True
        _update_known_status(context, message)
        frame = format_live_message(message)
        context.replay_state.record_message(message)
        return frame, False

    async def _guard_error(self, context: ActiveStreamContext) -> bytes | None:
        try:
            await context.guard.checkpoint()
        except StreamGuardViolation as exc:
            return _error_frame(exc.message, exc.code)
        return None

    async def _terminal_status_frame(self, context: ActiveStreamContext) -> bytes | None:
        state = context.live_state
        # 已见终态后不再加载 TaskRun：状态不可能继续变化。
        if state.known_status in TERMINAL_STATUSES:
            return None
        message = await self._dependencies.terminal_status_loader(context.run_id)
        if message is None:
            return None
        data = message.get("data") or {}
        status = str(data.get("status") or "")
        # P2 §4.2: 兜底覆盖所有状态变化（含 pending→running），仅与流内已知
        # 状态不同才下发，避免每轮缺口扫描重复推送同一状态帧。
        if not status or status == state.known_status:
            return None
        state.known_status = status
        return format_sse_event("run_status", message)


def _queue_error_frame(message: object) -> bytes | None:
    if message is QUEUE_OVERFLOW:
        return _error_frame("服务端推送积压，连接已重置，请重新连接", "overflow")
    if message is QUEUE_CAPACITY_UNAVAILABLE:
        return _error_frame("日志流容量协调服务不可用，请重新连接", "capacity_unavailable")
    return None


def _update_known_status(context: ActiveStreamContext, message: object) -> None:
    if not isinstance(message, dict) or message.get("type") != "run_status":
        return
    data = message.get("data") or {}
    if isinstance(data, dict) and data.get("status"):
        context.live_state.known_status = str(data["status"])


def _error_frame(message: str, code: str) -> bytes:
    return format_sse_event("stream_error", build_stream_error_message(message, code=code))


def _monotonic() -> float:
    return time.monotonic()


__all__ = ["ActiveLogStream", "ActiveStreamConfig", "ActiveStreamContext"]
__all__ += ["ActiveStreamDependencies", "LiveStreamState"]
