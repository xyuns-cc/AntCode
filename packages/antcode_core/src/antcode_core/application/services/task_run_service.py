"""
TaskRun 结果与状态更新服务

用于统一处理 Worker/Gateway 上报的执行结果与状态更新。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from loguru import logger

from antcode_core.domain.models.enums import DispatchStatus, RuntimeStatus
from antcode_core.domain.models.task_run import TaskRun
from antcode_core.application.services.scheduler.execution_status_service import (
    execution_status_service,
)


class TaskRunService:
    """TaskRun 状态与结果处理服务"""

    STATUS_MAPPING = {
        "queued": RuntimeStatus.QUEUED,
        "running": RuntimeStatus.RUNNING,
        "success": RuntimeStatus.SUCCESS,
        "succeeded": RuntimeStatus.SUCCESS,
        "failed": RuntimeStatus.FAILED,
        "timeout": RuntimeStatus.TIMEOUT,
        "timed_out": RuntimeStatus.TIMEOUT,
        "cancelled": RuntimeStatus.CANCELLED,
        "canceled": RuntimeStatus.CANCELLED,
        "skipped": RuntimeStatus.SKIPPED,
        "killed": RuntimeStatus.FAILED,
    }

    async def update_result(
        self,
        run_id: str,
        status: str,
        exit_code: int | None = None,
        error_message: str | None = None,
        output: str | None = None,
        started_at: datetime | str | None = None,
        finished_at: datetime | str | None = None,
        duration_ms: float | str | None = None,
        data: dict[str, Any] | None = None,
    ) -> bool:
        """更新执行结果"""
        execution = await self._get_execution(run_id)
        if not execution:
            logger.warning(f"执行记录不存在: {run_id}")
            return True

        runtime_status = self._normalize_status(status)
        if not runtime_status:
            logger.warning(f"无法识别的运行状态: {status}")
            return False

        start_dt = self._parse_dt(started_at)
        finish_dt = self._parse_dt(finished_at)
        status_at = finish_dt or start_dt or datetime.now(UTC)

        await execution_status_service.update_dispatch_status(
            execution_id=execution.execution_id,
            status=DispatchStatus.ACKED,
            status_at=status_at,
        )

        await execution_status_service.update_runtime_status(
            execution_id=execution.execution_id,
            status=runtime_status,
            status_at=status_at,
            exit_code=exit_code,
            error_message=error_message,
        )

        # 重新加载，避免覆盖状态字段
        execution = await self._get_execution(run_id)
        if not execution:
            return True

        # 同步额外字段
        if start_dt and not execution.start_time:
            execution.start_time = start_dt
        if finish_dt:
            execution.end_time = finish_dt
        if duration_ms and not execution.duration_seconds:
            duration_sec = self._to_float(duration_ms) / 1000.0
            execution.duration_seconds = duration_sec
        if exit_code is not None:
            execution.exit_code = exit_code
        if error_message:
            execution.error_message = error_message

        result_data = dict(execution.result_data or {})
        if output:
            result_data["output"] = output
        if data:
            result_data.update(data)
        if result_data:
            execution.result_data = result_data

        await execution.save()
        return True

    async def update_status(
        self,
        run_id: str,
        status: str,
        exit_code: int | None = None,
        error_message: str | None = None,
        status_at: datetime | str | None = None,
    ) -> bool:
        """更新运行状态（如 running）"""
        execution = await self._get_execution(run_id)
        if not execution:
            logger.warning(f"执行记录不存在: {run_id}")
            return False

        runtime_status = self._normalize_status(status)
        if not runtime_status:
            logger.warning(f"无法识别的运行状态: {status}")
            return False

        await execution_status_service.update_runtime_status(
            execution_id=execution.execution_id,
            status=runtime_status,
            status_at=self._parse_dt(status_at) or datetime.now(UTC),
            exit_code=exit_code,
            error_message=error_message,
        )
        return True

    async def _get_execution(self, run_id: str) -> TaskRun | None:
        execution = await TaskRun.get_or_none(execution_id=str(run_id))
        if execution:
            return execution
        return await TaskRun.get_or_none(public_id=str(run_id))

    def _normalize_status(self, status: str | RuntimeStatus) -> RuntimeStatus | None:
        if isinstance(status, RuntimeStatus):
            return status
        if not status:
            return None
        return self.STATUS_MAPPING.get(str(status).lower())

    def _parse_dt(self, value: datetime | str | None) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=UTC)
            return value
        try:
            parsed = datetime.fromisoformat(str(value))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed
        except Exception:
            return None

    def _to_float(self, value: float | str) -> float:
        try:
            return float(value)
        except Exception:
            return 0.0


task_run_service = TaskRunService()

__all__ = ["TaskRunService", "task_run_service"]
