"""
TaskRun 结果与状态更新服务

用于统一处理 Worker/Gateway 上报的执行结果与状态更新。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from loguru import logger
from tortoise.expressions import Q
from tortoise.transactions import in_transaction

from antcode_core.application.services.scheduler.execution_status_service import (
    execution_status_service,
)
from antcode_core.domain.models.enums import DispatchStatus, RuntimeStatus
from antcode_core.domain.models.task_run import TaskRun
from antcode_core.domain.models.worker import Worker


class TaskRunService:
    """TaskRun 状态与结果处理服务"""

    STATUS_MAPPING = {
        "queued": RuntimeStatus.QUEUED,
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
        """更新执行结果"""
        execution = await self._get_execution(run_id)
        if not execution:
            logger.warning(f"执行记录不存在: {run_id}")
            return False

        runtime_status = self._normalize_status(status)
        if not runtime_status:
            logger.warning(f"无法识别的运行状态: {status}")
            return False
        if not await self._validate_result_source(execution, worker_id, data, runtime_status):
            return False

        start_dt = self._parse_dt(started_at)
        finish_dt = self._parse_dt(finished_at)
        status_at = finish_dt or start_dt or datetime.now(UTC)

        dispatch_updated = await execution_status_service.update_dispatch_status(
            run_id=execution.run_id,
            status=DispatchStatus.ACKED,
            status_at=status_at,
        )

        runtime_updated = await execution_status_service.update_runtime_status(
            run_id=execution.run_id,
            status=runtime_status,
            status_at=status_at,
            exit_code=exit_code,
            error_message=error_message,
        )

        execution = await self._get_execution(run_id)
        if not execution:
            return False
        # P1-17 review: running/queued 是非终态"进度心跳"事件。多 master 消费组
        # 分区 / XAUTOCLAIM reclaim 都可能让它迟到（终态或 reconcile 的失败判定
        # 已先落库）。此时 CAS 被终态保护拒绝是**预期行为**，事件本身已无信息
        # 量——必须按"已消费"返回 True，让 result_loop 正常 XACK。否则该消息会
        # 重投 MAX_DELIVER_COUNT 次后进入 dead-letter stream，把良性心跳灌成
        # DLQ 噪音。终态不会被拉回：update_runtime_status 的终态吸收 + CAS
        # allowed_from（RUNNING 只允许来自 NULL/QUEUED）双重保证。
        # 终态结果的 CAS 冲突仍返回 False → 走 DLQ 保留结果正文（P1-19 设计）。
        is_progress_event = runtime_status in (RuntimeStatus.QUEUED, RuntimeStatus.RUNNING)
        if not runtime_updated and execution.runtime_status != runtime_status:
            if is_progress_event:
                logger.debug(
                    "忽略迟到的非终态状态事件: run_id={} current={} incoming={}",
                    run_id,
                    execution.runtime_status,
                    runtime_status,
                )
                return True
            logger.warning(
                "结果状态 CAS 冲突: run_id={} current={} incoming={}",
                run_id,
                execution.runtime_status,
                runtime_status,
            )
            return False
        if not dispatch_updated and execution.dispatch_status != DispatchStatus.ACKED:
            if is_progress_event:
                logger.debug(
                    "忽略 dispatch 已终态记录上的非终态状态事件: run_id={} dispatch={}",
                    run_id,
                    execution.dispatch_status,
                )
                return True
            logger.warning(
                "分发状态 CAS 冲突: run_id={} current={}",
                run_id,
                execution.dispatch_status,
            )
            return False
        return await self._update_result_metadata(
            run_id=execution.run_id,
            runtime_status=runtime_status,
            start_dt=start_dt,
            finish_dt=finish_dt,
            duration_ms=duration_ms,
            exit_code=exit_code,
            error_message=error_message,
            output=output,
            data=data,
        )

    async def _bind_lease_generation(self, execution: TaskRun, data: dict[str, Any] | None) -> bool:
        incoming = str((data or {}).get("lease_id") or "").strip()
        if not incoming:
            logger.warning(f"结果缺少 lease_id: run_id={execution.run_id}")
            return False
        if len(incoming) > 64:
            logger.warning(f"结果 lease_id 过长: run_id={execution.run_id}")
            return False
        updated = (
            await TaskRun.filter(run_id=execution.run_id)
            .filter(Q(lease_id__isnull=True) | Q(lease_id=incoming))
            .update(lease_id=incoming)
        )
        if not updated:
            logger.warning(
                "拒绝旧 Lease 结果: run_id={}",
                execution.run_id,
            )
        return bool(updated)

    # P1-GW-06: 终态状态集合。迟到放行分支仅对这些状态允许(见 _validate_result_lease
    # 注释),避免已撤销 L1 通过 late RUNNING/QUEUED 帧覆盖 L2 的真实进度。
    _TERMINAL_RUNTIME_STATUSES = frozenset(
        {
            RuntimeStatus.SUCCESS,
            RuntimeStatus.FAILED,
            RuntimeStatus.CANCELLED,
            RuntimeStatus.TIMEOUT,
            RuntimeStatus.SKIPPED,
        }
    )

    async def _validate_result_source(
        self,
        execution: TaskRun,
        worker_id: str | None,
        data: dict[str, Any] | None,
        incoming_status: RuntimeStatus | None = None,
    ) -> bool:
        declared_worker_id = str(worker_id or "").strip()
        if not declared_worker_id or not execution.worker_id:
            logger.warning(f"结果缺少已分配 Worker 身份: run_id={execution.run_id}")
            return False
        assigned = await Worker.filter(
            id=execution.worker_id,
            public_id=declared_worker_id,
        ).exists()
        if not assigned:
            logger.warning(
                "拒绝非归属 Worker 结果: run_id={} worker_id={}",
                execution.run_id,
                declared_worker_id,
            )
            return False
        return await self._validate_result_lease(execution, declared_worker_id, data, incoming_status)

    async def _validate_result_lease(
        self,
        execution: TaskRun,
        declared_worker_id: str,
        data: dict[str, Any] | None,
        incoming_status: RuntimeStatus | None = None,
    ) -> bool:
        incoming_lease_id = str((data or {}).get("lease_id") or "").strip()
        if not incoming_lease_id:
            logger.warning("结果缺少 lease_id: run_id={}", execution.run_id)
            return False
        if self._lease_validator is None:
            logger.error("结果租约校验器未配置，拒绝状态更新: run_id={}", execution.run_id)
            return False
        if not await self._lease_validator(declared_worker_id, incoming_lease_id):
            # 复审 D4: "消费时仍 current" 过宽 —— worker 正常完成后即关机/
            # 崩溃，lease 30s 自然过期；result_loop 积压时合法终态结果会被
            # 拒收进 DLQ，run 卡死后被 reconcile 误判失败重跑。若该 run 已
            # 绑定同一 lease 代际（绑定 CAS 只允许 NULL→X 或 X→X，旧代际
            # 无法抢绑），即便 lease 过期也接受本代际生前的结算。
            #
            # P1-GW-06: 收紧迟到放行分支 —— 仅当**且是终态帧**时才放行。
            # 之前放行任何状态,让被撤销的旧 L1 通过 late RUNNING/QUEUED 帧覆盖
            # 已绑定同代际的运行时状态,把 L2 的实际进度冲掉。终态帧本身幂等
            # (SUCCESS/FAILED/CANCELLED/TIMEOUT/SKIPPED),被吸收后不会再被拉回,
            # 因此可以安全放行。非终态帧一律拒收,让 result_loop XAUTOCLAIM 后
            # 交给当前 L2 重发。
            if (
                execution.lease_id
                and execution.lease_id == incoming_lease_id
                and incoming_status is not None
                and incoming_status in self._TERMINAL_RUNTIME_STATUSES
            ):
                logger.info(
                    "接受已绑定代际的迟到终态结算(lease 已过期): run_id={} worker_id={} status={}",
                    execution.run_id,
                    declared_worker_id,
                    incoming_status,
                )
                return True
            logger.warning(
                "拒绝非当前 Worker Lease 结果: run_id={} worker_id={} status={}",
                execution.run_id,
                declared_worker_id,
                incoming_status,
            )
            return False
        return await self._bind_lease_generation(execution, data)

    async def _update_result_metadata(
        self,
        *,
        run_id: str,
        runtime_status: RuntimeStatus,
        start_dt: datetime | None,
        finish_dt: datetime | None,
        duration_ms: float | str | None,
        exit_code: int | None,
        error_message: str | None,
        output: str | None,
        data: dict[str, Any] | None,
    ) -> bool:
        async with in_transaction("default") as conn:
            execution = await TaskRun.filter(run_id=run_id).using_db(conn).select_for_update().first()
            if execution is None or execution.runtime_status != runtime_status:
                return False
            updates = self._build_result_updates(
                execution,
                start_dt,
                finish_dt,
                duration_ms,
                exit_code,
                error_message,
                output,
                data,
            )
            if updates:
                await TaskRun.filter(id=execution.id).using_db(conn).update(**updates)
        return True

    def _build_result_updates(
        self,
        execution: TaskRun,
        start_dt: datetime | None,
        finish_dt: datetime | None,
        duration_ms: float | str | None,
        exit_code: int | None,
        error_message: str | None,
        output: str | None,
        data: dict[str, Any] | None,
    ) -> dict[str, Any]:
        updates: dict[str, Any] = {}
        if start_dt and not execution.start_time:
            updates["start_time"] = start_dt
        if finish_dt:
            updates["end_time"] = finish_dt
        if duration_ms and not execution.duration_seconds:
            updates["duration_seconds"] = self._to_float(duration_ms) / 1000.0
        if exit_code is not None:
            updates["exit_code"] = exit_code
        if error_message:
            updates["error_message"] = error_message
        result_data = dict(execution.result_data or {})
        result_data.pop("lease_id", None)
        if output:
            result_data["output"] = output
        if data:
            result_data.update({key: value for key, value in data.items() if key != "lease_id"})
        if result_data:
            updates["result_data"] = result_data
        return updates

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

    def _to_float(self, value: float | str) -> float:
        try:
            return float(value)
        except Exception:
            return 0.0


task_run_service = TaskRunService()

__all__ = ["TaskRunService", "task_run_service"]
