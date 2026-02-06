"""WebSocket 日志推送通知器。"""

from antcode_core.application.services.workers.log_notifier import LogRealtimeNotifier
from antcode_web_api.websockets.websocket_connection_manager import websocket_manager


class WebSocketLogNotifier(LogRealtimeNotifier):
    async def has_connections(self, execution_id: str) -> bool:
        return websocket_manager.get_connections_for_execution(execution_id) > 0

    async def send_log(
        self,
        execution_id: str,
        log_type: str,
        content: str,
        level: str,
    ) -> None:
        await websocket_manager.send_log_message(execution_id, log_type, content, level)

    async def send_status(
        self,
        execution_id: str,
        status: str,
        progress: float | None,
        message: str,
    ) -> None:
        await websocket_manager.send_execution_status(
            execution_id=execution_id,
            status=status,
            progress=progress,
            message=message,
        )
