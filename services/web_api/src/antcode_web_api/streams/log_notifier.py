"""SSE 日志推送通知器（Worker HTTP 上报路径 → broker）。

distributed_log_service 在 has_connections 为真时经此通道推送实时
log_line / run_status。注意这是与 Redis ingest stream 并列的第二条实时
路径（服务 HTTP 上报型 Worker），推送前日志已写入 PG，因此 sequence
必须透传——订阅端靠它过滤与历史快照的重叠。
"""

from __future__ import annotations

from antcode_core.application.services.workers.log_notifier import LogRealtimeNotifier

from antcode_web_api.streams.run_stream_broker import run_stream_broker
from antcode_web_api.streams.sse import build_log_line_message, build_run_status_message


class SSELogNotifier(LogRealtimeNotifier):
    async def has_connections(self, run_id: str) -> bool:
        return run_stream_broker.has_subscribers(run_id)

    async def send_log(
        self,
        *,
        run_id: str,
        log_type: str,
        content: str,
        level: str,
        sequence: int | None = None,
    ) -> None:
        run_stream_broker.publish(
            run_id,
            build_log_line_message(
                run_id,
                log_type=log_type,
                content=content,
                timestamp=None,
                sequence=sequence,
                source="task_execution",
            ),
        )

    async def send_status(
        self,
        *,
        run_id: str,
        status: str,
        progress: float | None,
        message: str,
    ) -> None:
        run_stream_broker.publish(
            run_id,
            build_run_status_message(
                run_id=run_id,
                status=status,
                progress=progress,
                message=message,
            ),
        )


__all__ = ["SSELogNotifier"]
