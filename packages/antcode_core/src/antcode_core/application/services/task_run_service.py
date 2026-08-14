"""
TaskRun 结果与状态更新服务

用于统一处理 Worker/Gateway 上报的执行结果与状态更新。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from antcode_core.application.services.result_status_contract import (
    ResultStatusContractError,
    validate_result_timing,
)
from antcode_core.application.services.scheduler.execution_status_service import (
    execution_status_service,
)
from antcode_core.application.services.task_result_commit import (
    ResultCommitOutcome,
    ResultCommitRequest,
    ResultMetadataRejected,
    TaskResultCommitter,
)
from antcode_core.domain.models.enums import RuntimeStatus
from antcode_core.domain.models.task_run import TaskRun

# P1-round6 5.3: result_data 单 run 总字节 hard cap, 同 result_metadata 常量
_MAX_RESULT_DATA_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class _ResultMetadata:
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: float | None
    exit_code: int | None
    error_message: str | None
    output: str | None
    data: dict[str, Any] | None


def _apply_bounded_result_data(current: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    """Merge result data or reject the entire result explicitly."""
    if not update:
        return current
    candidate = {**current, **update}
    try:
        candidate_size = len(json.dumps(candidate, ensure_ascii=False, default=str).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise ResultMetadataRejected("最终结果无法序列化，任务已标记失败") from exc
    if candidate_size > _MAX_RESULT_DATA_BYTES:
        raise ResultMetadataRejected(
            f"最终结果大小 {candidate_size} bytes 超过上限 {_MAX_RESULT_DATA_BYTES} bytes，任务已标记失败"
        )
    return candidate


class TaskRunService:
    """TaskRun 状态与结果处理服务"""

    STATUS_MAPPING = {
        "queued": RuntimeStatus.QUEUED,
        # Proto STATUS_PENDING 的 canonical 值是 pending；持久层没有独立
        # PENDING runtime 状态，按“已进入 Worker 等待队列”归一为 QUEUED。
        "pending": RuntimeStatus.QUEUED,
        "running": RuntimeStatus.RUNNING,
        "success": RuntimeStatus.SUCCESS,
        "succeeded": RuntimeStatus.SUCCESS,
        # P9: proto 契约的 canonical 字符串是 "completed"（见
        # antcode_contracts.transcode._PROTO_STATUS_TO_CANONICAL），
        # master.result_loop 走 proto_status_to_str 得到的就是 completed。
        # 之前 mapping 没有此键，每条 rule/code/file 任务的 result 都被
        # `无法识别的运行状态: completed` 打回并进 DLQ，PG task_executions
        # 永远停留在 pending。
        "completed": RuntimeStatus.SUCCESS,
        "done": RuntimeStatus.SUCCESS,
        "failed": RuntimeStatus.FAILED,
        "failure": RuntimeStatus.FAILED,
        "error": RuntimeStatus.FAILED,
        "timeout": RuntimeStatus.TIMEOUT,
        "timed_out": RuntimeStatus.TIMEOUT,
        "cancelled": RuntimeStatus.CANCELLED,
        "canceled": RuntimeStatus.CANCELLED,
        "skipped": RuntimeStatus.SKIPPED,
        "killed": RuntimeStatus.FAILED,
    }

    def __init__(self, lease_validator: Callable[[str, str], Awaitable[bool]] | None = None) -> None:
        self._lease_validator = lease_validator

    async def update_result(
        self,
        run_id: str,
        status: str,
        *,
        exit_code: int | None = None,
        error_message: str | None = None,
        output: str | None = None,
        started_at: datetime | str | None = None,
        finished_at: datetime | str | None = None,
        duration_ms: float | str | None = None,
        data: dict[str, Any] | None = None,
        worker_id: str | None = None,
    ) -> bool:
        """Compatibility API returning only whether the result was accepted."""
        outcome = await self.update_result_outcome(
            run_id=run_id,
            status=status,
            exit_code=exit_code,
            error_message=error_message,
            output=output,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            data=data,
            worker_id=worker_id,
        )
        return outcome.accepted

    async def update_result_outcome(
        self,
        run_id: str,
        status: str,
        *,
        exit_code: int | None = None,
        error_message: str | None = None,
        output: str | None = None,
        started_at: datetime | str | None = None,
        finished_at: datetime | str | None = None,
        duration_ms: float | str | None = None,
        data: dict[str, Any] | None = None,
        worker_id: str | None = None,
    ) -> ResultCommitOutcome:
        """Update a result and return its transaction-authoritative status."""
        runtime_status = self._normalize_status(status)
        if not runtime_status:
            logger.warning(f"无法识别的运行状态: {status}")
            return ResultCommitOutcome(False, str(run_id), None)
        try:
            timing = validate_result_timing(
                runtime_status,
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=duration_ms,
            )
        except ResultStatusContractError as exc:
            logger.warning("结果状态合同校验失败: run_id={} error={}", run_id, exc)
            return ResultCommitOutcome(False, str(run_id), None)
        start_dt = timing.started_at
        finish_dt = timing.finished_at
        status_at = finish_dt or start_dt or datetime.now(UTC)
        metadata = _ResultMetadata(
            started_at=start_dt,
            finished_at=finish_dt,
            duration_ms=timing.duration_ms,
            exit_code=exit_code,
            error_message=error_message,
            output=output,
            data=data,
        )
        request = ResultCommitRequest(
            run_id=str(run_id),
            worker_id=str(worker_id or ""),
            lease_id=str((data or {}).get("lease_id") or ""),
            runtime_status=runtime_status,
            status_at=status_at,
            exit_code=exit_code,
            error_message=error_message,
            metadata_builder=lambda execution: self._build_result_updates(execution, metadata),
        )
        return await TaskResultCommitter(self._lease_validator).commit_outcome(request)

    def _build_result_updates(
        self,
        execution: TaskRun,
        metadata: _ResultMetadata,
    ) -> dict[str, Any]:
        updates: dict[str, Any] = {}
        if metadata.started_at and not execution.start_time:
            updates["start_time"] = metadata.started_at
        if metadata.finished_at and not execution.end_time:
            updates["end_time"] = metadata.finished_at
        duration_seconds = self._result_duration_seconds(
            execution,
            start_dt=metadata.started_at,
            finish_dt=metadata.finished_at,
            duration_ms=metadata.duration_ms,
        )
        if duration_seconds is not None:
            updates["duration_seconds"] = duration_seconds
        if metadata.exit_code is not None:
            updates["exit_code"] = metadata.exit_code
        if metadata.error_message:
            updates["error_message"] = metadata.error_message
        result_data = dict(execution.result_data or {})
        result_data.pop("lease_id", None)
        candidate_update: dict[str, Any] = {}
        if metadata.output:
            candidate_update["output"] = metadata.output
        if metadata.data:
            candidate_update.update({key: value for key, value in metadata.data.items() if key != "lease_id"})
        result_data = _apply_bounded_result_data(result_data, candidate_update)
        if result_data:
            updates["result_data"] = result_data
        return updates

    @staticmethod
    def _result_duration_seconds(
        execution: TaskRun,
        *,
        start_dt: datetime | None,
        finish_dt: datetime | None,
        duration_ms: float | None,
    ) -> float | None:
        if execution.duration_seconds is not None:
            return None
        effective_start = getattr(execution, "start_time", None) or start_dt
        effective_finish = getattr(execution, "end_time", None) or finish_dt
        if effective_start is not None and effective_finish is not None:
            return (effective_finish - effective_start).total_seconds()
        if duration_ms is None:
            return None
        return duration_ms / 1000.0

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

        return await execution_status_service.update_runtime_status(
            run_id=execution.run_id,
            status=runtime_status,
            status_at=self._parse_dt(status_at) or datetime.now(UTC),
            exit_code=exit_code,
            error_message=error_message,
        )

    async def _get_execution(self, run_id: str) -> TaskRun | None:
        run_id_str = str(run_id)
        execution = await TaskRun.get_or_none(run_id=run_id_str)
        if execution:
            return execution

        # public_id 固定为 32 字符；超长 run_id 不应回退到 public_id 查询
        if len(run_id_str) > 32:
            return None

        return await TaskRun.get_or_none(public_id=run_id_str)

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


task_run_service = TaskRunService()

__all__ = ["TaskRunService", "task_run_service"]
