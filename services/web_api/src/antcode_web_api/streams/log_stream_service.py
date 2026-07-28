"""SSE 日志流编排服务。

先注册实时队列再读 PG 快照。历史用于首屏展示但不推进断线游标；恢复与
存活连接缺口扫描以 ``task_logs.id`` 为唯一水位，实时帧只记录精确身份，
待 PG 固定快照确认后再发 ``pg:<storage_id>`` checkpoint。

安全边界（对齐原 WebSocket 连接管理器，P1-09）：
- 周期会话重校验：登出 / revoke_all_sessions 后 ≤60s 内终止存活流；
- 最大流寿命 8h，超时终止（客户端会自动重连换新 ticket）；
- 慢消费者队列溢出即终止（客户端重连拿全量历史）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from antcode_core.common.config import settings
from antcode_core.domain.models.task_run import TaskRun
from loguru import logger

from antcode_web_api.streams import log_stream_active
from antcode_web_api.streams.ingest_event_id import LogStreamCursor
from antcode_web_api.streams.ingest_follower import ingest_log_follower
from antcode_web_api.streams.ingest_recovery import RecoveryWindow, ingest_recovery_reader
from antcode_web_api.streams.ingest_recovery_query import RecoveryCursorExpiredError
from antcode_web_api.streams.log_stream_access import (
    build_current_status_message,
    execution_access_still_valid,
    load_current_status_message,
    verify_execution_access,
)
from antcode_web_api.streams.log_stream_access import (
    session_still_valid as _session_still_valid,
)
from antcode_web_api.streams.log_stream_active import (
    ActiveLogStream,
    ActiveStreamConfig,
    ActiveStreamContext,
    ActiveStreamDependencies,
    LiveStreamState,
)
from antcode_web_api.streams.log_stream_gap import LogGapReader, postgres_log_gap_reader
from antcode_web_api.streams.log_stream_guard import (
    StreamGuard,
    StreamGuardConfig,
    StreamGuardDependencies,
    StreamGuardViolation,
)
from antcode_web_api.streams.log_stream_history import BoundedHistoryReplay, HistoryReplayError
from antcode_web_api.streams.log_stream_recovery import BoundedRecoveryReplay
from antcode_web_api.streams.log_stream_replay import (
    ReplayState,
    stream_cursor_frame,
)
from antcode_web_api.streams.run_stream_broker import (
    StreamCapacityUnavailableError,
    StreamLimitExceededError,
    StreamSubscription,
    run_stream_broker,
)
from antcode_web_api.streams.sse import (
    build_stream_error_message,
    format_sse_event,
)

PING_INTERVAL_SECONDS = 15.0
GAP_CHECK_INTERVAL_SECONDS = 5.0
# 实时帧持续到达（broker 健康）或已见终态时，缺口扫描退避到该间隔，
# 避免每连接每 5s 一次 MAX(id)/COUNT 扫描随并发观看者线性放大 PG 负载。
GAP_CHECK_BACKOFF_INTERVAL_SECONDS = 30.0
SESSION_RECHECK_INTERVAL_SECONDS = 60.0
MAX_STREAM_LIFETIME_SECONDS = 8 * 3600
HISTORY_LIMIT = 10000
HISTORY_MAX_BYTES = int(settings.SSE_HISTORY_MAX_BYTES)


class LogStreamService:
    """SSE 日志流编排。"""

    def __init__(self, gap_reader: LogGapReader = postgres_log_gap_reader) -> None:
        self._gap_reader = gap_reader

    async def stream(
        self,
        run_id: str,
        execution: TaskRun,
        *,
        user_id: int,
        session_jti: str,
        cursor: LogStreamCursor | None = None,
    ) -> AsyncIterator[bytes]:
        """SSE 帧生成器。权限校验须在调用前完成（HTTP 状态码语义）。"""
        guard = self._stream_guard(run_id, user_id, session_jti)
        try:
            subscription = await run_stream_broker.subscribe(run_id, user_id)
        except StreamLimitExceededError as exc:
            # 路由层 ensure_capacity 已预检 429；此处兜底并发竞态窗口
            yield format_sse_event("stream_error", build_stream_error_message(str(exc), code="limit"))
            return
        except StreamCapacityUnavailableError as exc:
            yield _stream_error(str(exc), "capacity_unavailable")
            return

        followed = False
        try:
            follower_error = await self._start_follower(run_id)
            if follower_error is not None:
                yield follower_error
                return
            followed = True
            async for frame in self._active_stream_frames(
                run_id,
                execution,
                subscription,
                cursor=cursor,
                guard=guard,
            ):
                yield frame
        finally:
            await self._release_stream(subscription, run_id, followed)

    async def _active_stream_frames(
        self,
        run_id: str,
        execution: TaskRun,
        subscription: StreamSubscription,
        *,
        cursor: LogStreamCursor | None,
        guard: StreamGuard,
    ) -> AsyncIterator[bytes]:
        yield format_sse_event("run_status", build_current_status_message(run_id, execution))
        replay_state = ReplayState()
        try:
            if cursor is None:
                history = BoundedHistoryReplay(
                    ingest_log_follower.history_reader,
                    guard,
                    run_id=run_id,
                    replay_state=replay_state,
                    max_lines=HISTORY_LIMIT,
                    max_bytes=HISTORY_MAX_BYTES,
                )
                async for frame in history.frames():
                    yield frame
            else:
                should_stop = False
                async for frame, terminal in self._resume_frames(
                    run_id, cursor=cursor, replay_state=replay_state, guard=guard
                ):
                    yield frame
                    should_stop = terminal
                if should_stop:
                    return
        except StreamGuardViolation as exc:
            yield _stream_error(exc.message, exc.code)
            return
        except HistoryReplayError:
            yield _stream_error("历史日志暂时不可用，请稍后重试", "history_unavailable")
            return
        async for frame in self._realtime_frames(
            run_id,
            subscription,
            replay_state,
            guard=guard,
            known_status=_execution_status(execution),
        ):
            yield frame

    async def _start_follower(self, run_id: str) -> bytes | None:
        try:
            await ingest_log_follower.follow(run_id)
        except Exception as exc:
            logger.exception("日志 follower 启动失败 run_id={}: {}", run_id, exc)
            return format_sse_event(
                "stream_error",
                build_stream_error_message("实时日志服务不可用，请稍后重试", code="follower_unavailable"),
            )
        return None

    async def _resume_frames(
        self,
        run_id: str,
        *,
        cursor: LogStreamCursor,
        replay_state: ReplayState,
        guard: StreamGuard,
    ) -> AsyncIterator[tuple[bytes, bool]]:
        try:
            await guard.checkpoint()
            window = await ingest_recovery_reader.materialize_after(run_id, cursor)
        except StreamGuardViolation:
            raise
        except RecoveryCursorExpiredError:
            yield _stream_error("日志断点已过期，重新加载当前历史窗口", "cursor_expired"), True
            return
        except Exception as exc:
            logger.exception("SSE 断线恢复读取失败 run_id={} cursor={}: {}", run_id, cursor.event_id, exc)
            yield _stream_error("断线日志暂时无法恢复，请稍后重试", "recovery_unavailable"), True
            return
        replay_state.advance_storage_watermark(window.start_id)
        replay = self._bounded_recovery(run_id, window=window, replay_state=replay_state, guard=guard)
        async for frame in replay.frames():
            yield frame, False
        if replay.overflowed:
            yield _stream_error("断线日志超过单次恢复容量，将从当前断点继续", "recovery_overflow"), True
            return
        replay_state.advance_storage_watermark(window.snapshot_id)
        if window.snapshot_id > window.start_id:
            yield stream_cursor_frame(window.snapshot_id), False

    @staticmethod
    def _bounded_recovery(
        run_id: str,
        *,
        window: RecoveryWindow,
        replay_state: ReplayState,
        guard: StreamGuard,
    ) -> BoundedRecoveryReplay:
        return BoundedRecoveryReplay(
            ingest_recovery_reader,
            guard,
            run_id=run_id,
            window=window,
            replay_state=replay_state,
            max_lines=HISTORY_LIMIT,
            max_bytes=HISTORY_MAX_BYTES,
        )

    async def _release_stream(
        self,
        subscription: StreamSubscription,
        run_id: str,
        followed: bool,
    ) -> None:
        try:
            await run_stream_broker.unsubscribe(subscription)
        finally:
            if followed:
                await self._stop_follower(run_id)

    async def _stop_follower(self, run_id: str) -> None:
        try:
            await ingest_log_follower.unfollow(run_id)
        except Exception as exc:
            logger.exception("日志 follower 清理失败 run_id={}: {}", run_id, exc)

    async def _realtime_frames(
        self,
        run_id: str,
        subscription: StreamSubscription,
        replay_state: ReplayState,
        *,
        guard: StreamGuard,
        known_status: str,
    ) -> AsyncIterator[bytes]:
        active_stream = ActiveLogStream(
            ActiveStreamDependencies(
                broker=run_stream_broker,
                gap_reader=self._gap_reader,
                terminal_status_loader=load_current_status_message,
            ),
            ActiveStreamConfig(
                ping_interval=PING_INTERVAL_SECONDS,
                gap_check_interval=GAP_CHECK_INTERVAL_SECONDS,
                gap_check_backoff_interval=GAP_CHECK_BACKOFF_INTERVAL_SECONDS,
            ),
        )
        context = ActiveStreamContext(run_id, subscription, replay_state, guard, LiveStreamState(known_status))
        async for frame in active_stream.frames(context):
            yield frame

    @staticmethod
    def _stream_guard(run_id: str, user_id: int, session_jti: str) -> StreamGuard:
        dependencies = StreamGuardDependencies(_session_still_valid, execution_access_still_valid)
        config = StreamGuardConfig(MAX_STREAM_LIFETIME_SECONDS, SESSION_RECHECK_INTERVAL_SECONDS)
        # 与存活阶段（log_stream_active）共用同一单调时钟：deadline 与
        # 会话重校验由一个可脚本化时钟统一驱动，测试只需替换一处。
        clock = log_stream_active._monotonic
        return StreamGuard(
            dependencies,
            config,
            run_id=run_id,
            user_id=user_id,
            session_jti=session_jti,
            started_at=clock(),
            monotonic=clock,
        )


def _stream_error(message: str, code: str) -> bytes:
    return format_sse_event("stream_error", build_stream_error_message(message, code=code))


def _execution_status(execution: TaskRun) -> str:
    return execution.status.value if execution.status else "queued"


log_stream_service = LogStreamService()

__all__ = [
    "GAP_CHECK_BACKOFF_INTERVAL_SECONDS",
    "GAP_CHECK_INTERVAL_SECONDS",
    "HISTORY_LIMIT",
    "MAX_STREAM_LIFETIME_SECONDS",
]
__all__ += ["PING_INTERVAL_SECONDS", "SESSION_RECHECK_INTERVAL_SECONDS"]
__all__ += ["LogStreamService", "build_current_status_message", "log_stream_service", "verify_execution_access"]
