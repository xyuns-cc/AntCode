"""日志实时推送通知器协议定义。"""

from typing import Protocol


class LogRealtimeNotifier(Protocol):
    async def has_connections(self, execution_id: str) -> bool:
        """是否存在订阅该执行的连接。"""

    async def send_log(
        self,
        execution_id: str,
        log_type: str,
        content: str,
        level: str,
    ) -> None:
        """推送日志内容。"""

    async def send_status(
        self,
        execution_id: str,
        status: str,
        progress: float | None,
        message: str,
    ) -> None:
        """推送执行状态。"""
