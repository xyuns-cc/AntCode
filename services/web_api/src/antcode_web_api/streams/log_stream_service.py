"""SSE 日志流编排服务。

单个 SSE 连接的完整生命周期（帧序列与原 WebSocket 协议一致）：

    run_status → historical_logs_start → log_line*
    → historical_logs_end | no_historical_logs → (实时 log_line / run_status)*

正确性设计（前端因此无需任何去重）：
1. 先注册 broker 队列（实时帧开始入队），再确保 ingest 跟随，最后读 PG
   历史快照——快照期间到达的实时帧全部在队列里；
2. 历史发送完后记录各 log_type 的最大 sequence，队列中 sequence 落在
   阈值内的帧被过滤（历史/实时重叠消除）。sequence 在 (run_id, log_type)
   内单调，静态阈值即正确；sequence=0 合法（阈值缺省 -1）；
3. 无 sequence 的帧（旧 JSON 等异常路径）直通。

安全边界（对齐原 WebSocket 连接管理器，P1-09）：
- 周期会话重校验：登出 / revoke_all_sessions 后 ≤60s 内终止存活流；
- 最大流寿命 8h，超时终止（客户端会自动重连换新 ticket）；
- 慢消费者队列溢出即终止（客户端重连拿全量历史）。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from antcode_core.application.services.projects.relation_service import relation_service
from antcode_core.domain.models import User, UserSession
from antcode_core.domain.models.task_run import TaskRun
from fastapi import HTTPException
from loguru import logger

from antcode_web_api.streams.ingest_follower import ingest_log_follower
from antcode_web_api.streams.run_stream_broker import (
    QUEUE_OVERFLOW,
    StreamLimitExceededError,
    StreamSubscription,
    run_stream_broker,
)
from antcode_web_api.streams.sse import (
    build_history_complete_message,
    build_log_line_message,
    build_ping_message,
    build_run_status_message,
    build_stream_error_message,
    format_sse_event,
)

PING_INTERVAL_SECONDS = 15.0
SESSION_RECHECK_INTERVAL_SECONDS = 60.0
MAX_STREAM_LIFETIME_SECONDS = 8 * 3600
HISTORY_LIMIT = 10000

TERMINAL_STATUSES = {"success", "failed", "timeout", "cancelled", "skipped", "rejected"}


class _HistoryUnavailableError(Exception):
    """历史读取失败（DB 故障等），流应发 stream_error 后终止。"""


async def verify_execution_access(run_id: str, user: User) -> TaskRun:
    """验证用户对执行记录的访问权限（在流式响应开始前调用，403/404 直出）。"""
    # 执行记录可能还在创建中，等待最多 5 秒
    execution = None
    for _ in range(10):
        execution = await TaskRun.get_or_none(run_id=run_id)
        if execution:
            break
        await asyncio.sleep(0.5)

    if not execution:
        raise HTTPException(status_code=404, detail="执行记录不存在")

    # 管理员可以访问所有执行记录
    if user.is_admin:
        return execution

    task = await relation_service.get_task_by_id(execution.task_id)
    if not task:
        # 任务已删除，只有管理员可以访问
        raise HTTPException(status_code=404, detail="关联任务不存在或已删除")

    if task.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权访问此执行记录")
    return execution


def build_current_status_message(run_id: str, execution: TaskRun) -> dict:
    """连接建立即刻推送的当前执行状态帧（终态 progress=100）。"""
    status = execution.status.value if execution.status else "queued"

    message = f"当前状态: {status}"
    if status == "running":
        message = "任务正在执行中"
    elif status == "success":
        message = "任务执行成功"
    elif status == "failed":
        message = "任务执行失败"
    elif status == "queued":
        message = "任务排队中"

    return build_run_status_message(
        run_id=run_id,
        status=status,
        progress=100.0 if status in TERMINAL_STATUSES else None,
        message=message,
    )


async def _session_still_valid(user_id: int, session_jti: str) -> bool:
    """周期会话重校验（P1-09）。

    DB 异常时 fail-open（与原 WS 连接管理器语义对齐）：短暂 PG 抖动不应
    集体杀掉全部存活流——重连路径同样依赖 PG，抖动期间前端重连必然失败并
    在 5 次退避后永久 failed。安全性由 8h 最大流寿命兜底。
    """
    try:
        session = await UserSession.filter(
            jti=session_jti,
            user_id=user_id,
            revoked_at__isnull=True,
        ).first()
        if session is None:
            return False
        user = await User.get_or_none(id=user_id)
        return user is not None and getattr(user, "is_active", True)
    except Exception as e:
        logger.warning("日志流会话重校验失败（DB 异常，本轮跳过）: user_id={} err={}", user_id, e)
        return True


class LogStreamService:
    """SSE 日志流编排。"""

    async def stream(
        self,
        run_id: str,
        execution: TaskRun,
        *,
        user_id: int,
        session_jti: str,
    ) -> AsyncIterator[bytes]:
        """SSE 帧生成器。权限校验须在调用前完成（HTTP 状态码语义）。"""
        try:
            subscription = run_stream_broker.subscribe(run_id, user_id)
        except StreamLimitExceededError as exc:
            # 路由层 ensure_capacity 已预检 429；此处兜底并发竞态窗口
            yield format_sse_event("stream_error", build_stream_error_message(str(exc), code="limit"))
            return

        followed = False
        try:
            await ingest_log_follower.follow(run_id)
            followed = True

            # 当前执行状态（首帧尽快 flush，前端立即拿到最新状态）
            yield format_sse_event("run_status", build_current_status_message(run_id, execution))

            # 历史回放（队列已注册：期间到达的实时帧全部入队，之后按阈值过滤）
            max_history_seq: dict[str, int] = {}
            try:
                async for frame in self._history_frames(run_id, max_history_seq):
                    yield frame
            except _HistoryUnavailableError:
                # DB 故障必须对客户端可见：伪装成"无历史日志"会让已有几千行
                # 日志的 run 凭空显示为空且无任何错误提示（客户端重连自愈）
                yield format_sse_event(
                    "stream_error",
                    build_stream_error_message("历史日志暂时不可用，请稍后重试", code="history_unavailable"),
                )
                return

            # 实时消费（ping 保活 + 周期会话重校验 + 最大寿命）
            async for frame in self._realtime_frames(
                subscription,
                max_history_seq,
                user_id=user_id,
                session_jti=session_jti,
            ):
                yield frame
        finally:
            # 客户端断连（CancelledError/GeneratorExit）也走这里
            if followed:
                await ingest_log_follower.unfollow(run_id)
            run_stream_broker.unsubscribe(subscription)

    async def _history_frames(
        self,
        run_id: str,
        max_history_seq: dict[str, int],
    ) -> AsyncIterator[bytes]:
        """历史回放帧；同时按 log_type 记录最大 sequence 供实时重叠过滤。"""
        yield format_sse_event(
            "historical_logs_start",
            {"type": "historical_logs_start", "timestamp": _now_iso()},
        )
        try:
            history = await ingest_log_follower.fetch_history(run_id, limit=HISTORY_LIMIT)
        except Exception as e:
            logger.warning("日志流历史读取失败 run_id={}: {}", run_id, e)
            raise _HistoryUnavailableError() from e

        sent = 0
        for entry in history:
            message = build_log_line_message(
                run_id,
                log_type=entry["log_type"],
                content=entry["content"],
                timestamp=entry["timestamp"] or None,
                sequence=entry["sequence"],
                source=entry.get("source", "pg_history"),
            )
            yield format_sse_event("log_line", message)
            sent += 1
            seq = entry["sequence"]
            # sequence=0 不抬阈值：master 写入的系统行（派发/状态消息）sequence
            # 缺省 0，若以此为阈值，worker 真实的 seq=0 首行会被 0<=0 误滤。
            # 代价是"worker 仅有 seq=0 一行且恰好同时在快照与队列"这一极窄
            # 窗口下重复显示一行（重复优于丢失）。
            if seq is not None and seq > 0:
                log_type = entry["log_type"]
                max_history_seq[log_type] = max(max_history_seq.get(log_type, -1), seq)

        complete = build_history_complete_message(sent, truncated=sent >= HISTORY_LIMIT)
        yield format_sse_event(complete["type"], complete)
        logger.debug("日志流历史回放完成: run_id={} sent={}", run_id, sent)

    async def _realtime_frames(
        self,
        subscription: StreamSubscription,
        max_history_seq: dict[str, int],
        *,
        user_id: int,
        session_jti: str,
    ) -> AsyncIterator[bytes]:
        started = time.monotonic()
        next_recheck = started + SESSION_RECHECK_INTERVAL_SECONDS
        while True:
            now = time.monotonic()
            if now - started > MAX_STREAM_LIFETIME_SECONDS:
                yield format_sse_event(
                    "stream_error",
                    build_stream_error_message("连接超过最大生存时长，请重新连接", code="max_lifetime"),
                )
                return
            if now >= next_recheck:
                next_recheck = now + SESSION_RECHECK_INTERVAL_SECONDS
                if not await _session_still_valid(user_id, session_jti):
                    yield format_sse_event(
                        "stream_error",
                        build_stream_error_message("会话已失效，连接终止", code="session_revoked"),
                    )
                    return

            try:
                message = await asyncio.wait_for(
                    subscription.queue.get(),
                    timeout=PING_INTERVAL_SECONDS,
                )
            except TimeoutError:
                yield format_sse_event("ping", build_ping_message())
                continue

            if message is QUEUE_OVERFLOW:
                yield format_sse_event(
                    "stream_error",
                    build_stream_error_message("服务端推送积压，连接已重置，请重新连接", code="overflow"),
                )
                return

            if self._is_history_overlap(message, max_history_seq):
                continue

            yield format_sse_event(str(message.get("type") or "log_line"), message)

    @staticmethod
    def _is_history_overlap(message: dict, max_history_seq: dict[str, int]) -> bool:
        """历史/实时重叠判定（sequence=0 合法，不能用真值判断）。"""
        if message.get("type") != "log_line":
            return False
        data = message.get("data") or {}
        seq = data.get("sequence")
        if seq is None:
            return False
        return seq <= max_history_seq.get(data.get("log_type") or "stdout", -1)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


log_stream_service = LogStreamService()

__all__ = [
    "HISTORY_LIMIT",
    "MAX_STREAM_LIFETIME_SECONDS",
    "PING_INTERVAL_SECONDS",
    "SESSION_RECHECK_INTERVAL_SECONDS",
    "LogStreamService",
    "build_current_status_message",
    "log_stream_service",
    "verify_execution_access",
]
