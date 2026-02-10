"""WebSocket 日志推送通知器。"""

from antcode_core.application.services.workers.log_notifier import LogRealtimeNotifier
from antcode_web_api.websockets.websocket_connection_manager import websocket_manager


class WebSocketLogNotifier(LogRealtimeNotifier):
    async def has_connections(self, run_id: str) -> bool:
        return websocket_manager.get_connections_for_run(run_id) > 0

    async def send_log(
        self,
        run_id: str,
        log_type: str,
        content: str,
        level: str,
    ) -> None:
        await websocket_manager.send_log_message(run_id=run_id, log_type=log_type, content=content, level=level)

    async def send_status(
        self,
        run_id: str,
        status: str,
        progress: float | None,
        message: str,
    ) -> None:
        await websocket_manager.send_run_status(
            run_id=run_id,
            status=status,
            progress=progress,
            message=message,
        )
