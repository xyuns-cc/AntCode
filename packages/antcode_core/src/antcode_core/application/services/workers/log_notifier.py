"""日志实时推送通知器协议定义。"""

from typing import Protocol


class LogRealtimeNotifier(Protocol):
    async def has_connections(self, run_id: str) -> bool:
        """是否存在订阅该执行的连接。"""

    async def send_log(
        self,
        *,
        run_id: str,
        log_type: str,
        content: str,
        level: str,
        sequence: int | None = None,
        storage_id: int,
    ) -> None:
        """推送带 PG 主键的已持久化日志，供订阅端过滤历史/实时重叠。"""

    async def send_status(
        self,
        *,
        run_id: str,
        status: str,
        progress: float | None,
        message: str,
    ) -> None:
        """推送执行状态。"""
