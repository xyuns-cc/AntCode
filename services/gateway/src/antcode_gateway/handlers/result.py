"""
结果处理器

接收 Worker 的任务执行结果，写入 Redis Streams，由 Master 消费。

**Validates: Requirements 6.6**
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from loguru import logger

from antcode_core.common.config import settings
from antcode_core.infrastructure.redis.streams import StreamClient


@dataclass
class TaskResult:
    """任务结果"""

    run_id: str
    task_id: str
    status: str  # success, failed, timeout, cancelled
    exit_code: int = 0
    error_message: str = ""
    output: str = ""
    data: dict | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int = 0


class ResultHandler:
    """结果处理器

    接收 Worker 的任务执行结果：
    1. 写入 Redis Streams
    """

    def __init__(self):
        """初始化处理器"""
        self._stream = StreamClient()
        self._result_stream = f"{settings.REDIS_NAMESPACE}:task:result"

    async def handle(self, result: TaskResult) -> bool:
        """处理任务结果

        Args:
            result: 任务结果

        Returns:
            是否成功
        """
        run_id = result.run_id
        status = result.status

        logger.info(
            f"收到任务结果: run_id={run_id}, "
            f"status={status}, exit_code={result.exit_code}"
        )

        payload = self._build_payload(result)
        return await self._publish_result(payload)

    async def _publish_result(self, payload: dict) -> bool:
        """写入 Redis Streams"""
        try:
            await self._stream.xadd(self._result_stream, payload)
            return True
        except Exception as e:
            logger.error(f"写入结果流失败: {e}")
            return False

    def _build_payload(self, result: TaskResult) -> dict:
        payload = {
            "run_id": result.run_id,
            "task_id": result.task_id,
            "status": result.status,
            "exit_code": result.exit_code,
            "error_message": result.error_message,
            "started_at": self._format_dt(result.started_at),
            "finished_at": self._format_dt(result.finished_at),
            "duration_ms": result.duration_ms,
            "data": result.data or {},
        }
        return {k: v for k, v in payload.items() if v not in (None, "", {})}

    def _format_dt(self, value: datetime | None) -> str | None:
        if not value:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.isoformat()

    async def handle_status_update(
        self,
        run_id: str,
        status: str,
        exit_code: int | None = None,
        error_message: str | None = None,
        timestamp: datetime | None = None,
    ) -> bool:
        """处理状态更新

        用于处理中间状态更新（如 running）。

        Args:
            run_id: 运行 ID
            status: 状态
            exit_code: 退出码
            error_message: 错误消息
            timestamp: 时间戳

        Returns:
            是否成功
        """
        logger.debug(
            f"收到状态更新: run_id={run_id}, status={status}"
        )

        payload = {
            "run_id": run_id,
            "status": status,
            "exit_code": exit_code,
            "error_message": error_message or "",
            "started_at": self._format_dt(timestamp or datetime.now(UTC)),
        }
        return await self._publish_result({k: v for k, v in payload.items() if v not in (None, "")})
