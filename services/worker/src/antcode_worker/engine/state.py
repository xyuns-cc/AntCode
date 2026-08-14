"""
任务状态管理

Requirements: 4.3
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from loguru import logger

from antcode_worker.domain.models import ExecResult


class RunState(StrEnum):
    """运行状态"""

    QUEUED = "queued"  # 在本地队列中
    PREPARING = "preparing"  # 准备运行时
    RUNNING = "running"  # 执行中
    CANCELLING = "cancelling"  # 取消中
    COMPLETED = "completed"  # 已完成
    FAILED = "failed"  # 失败
    CANCELLED = "cancelled"  # 已取消


@dataclass
class RunInfo:
    """运行信息"""

    run_id: str
    task_id: str
    state: RunState = RunState.QUEUED
    receipt: str | None = None

    # 时间
    queued_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    # 结果
    exit_code: int | None = None
    error: str | None = None

    # 元数据
    data: dict[str, Any] = field(default_factory=dict)

    # 任务执行结束后、结果与源消息全部结算前保留的恢复信息。
    settlement_result: ExecResult | None = None
    settlement_active: bool = False
    result_reported: bool = False
    pending_receipts: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class CancelRequest:
    """Snapshot captured while atomically marking a run for cancellation."""

    run_id: str
    task_id: str
    state: RunState
    receipt: str | None
    queued_at: datetime | None


class StateManager:
    """
    状态管理器

    线程安全地管理所有运行中任务的状态。

    Requirements: 4.3
    """

    # 有效的状态转换
    VALID_TRANSITIONS = {
        RunState.QUEUED: {RunState.PREPARING, RunState.CANCELLED},
        RunState.PREPARING: {RunState.RUNNING, RunState.FAILED, RunState.CANCELLED},
        RunState.RUNNING: {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLING},
        RunState.CANCELLING: {RunState.CANCELLED, RunState.COMPLETED, RunState.FAILED},
        RunState.COMPLETED: set(),
        RunState.FAILED: set(),
        RunState.CANCELLED: set(),
    }

    def __init__(self):
        self._runs: dict[str, RunInfo] = {}
        self._lock = asyncio.Lock()

    async def add(self, run_id: str, task_id: str, receipt: str | None = None) -> RunInfo:
        """添加新运行；已存在直接返回旧的（保留旧行为，供 legacy 调用点）。"""
        info, _ = await self.add_if_new(run_id, task_id, receipt=receipt)
        return info

    async def add_if_new(self, run_id: str, task_id: str, receipt: str | None = None) -> tuple[RunInfo, bool]:
        """B2: add 的原子变体，返回 ``(info, is_new)`` 让调用方判断是否重复。

        engine._worker_loop 需要根据 is_new 决定是否 enqueue —— 否则 reclaim
        取回旧消息或 direct 重投时会双跑同 run_id。
        """
        async with self._lock:
            if run_id in self._runs:
                return self._runs[run_id], False

            info = RunInfo(
                run_id=run_id,
                task_id=task_id,
                receipt=receipt,
                queued_at=datetime.now(),
            )
            if receipt:
                info.pending_receipts.add(receipt)
            self._runs[run_id] = info
            logger.debug(f"添加运行: {run_id}")
            return info, True

    async def start_settlement(
        self,
        run_id: str,
        *,
        task_id: str,
        receipt: str | None,
        result: ExecResult,
    ) -> None:
        """保存执行结果并占用结算权，供失败后的重投继续结算。"""
        async with self._lock:
            info = self._runs.get(run_id)
            if info is None:
                info = RunInfo(run_id=run_id, task_id=task_id, receipt=receipt, queued_at=datetime.now())
                self._runs[run_id] = info
            if info.settlement_result is not None and info.settlement_result != result:
                raise RuntimeError(f"运行存在冲突的待结算结果: {run_id}")
            if receipt:
                info.pending_receipts.add(receipt)
                info.receipt = receipt
            info.settlement_result = result
            info.settlement_active = True

    async def reserve_settlement_recovery(self, run_id: str, receipt: str | None) -> bool:
        """登记重投 receipt；待结算且当前无处理者时取得恢复权。"""
        async with self._lock:
            info = self._require_run(run_id)
            if receipt:
                info.pending_receipts.add(receipt)
                info.receipt = receipt
            if info.settlement_result is None or info.settlement_active:
                return False
            info.settlement_active = True
            return True

    async def settlement_snapshot(self, run_id: str) -> tuple[ExecResult, bool, tuple[str, ...]]:
        """返回不可变的待结算快照。"""
        async with self._lock:
            info = self._require_run(run_id)
            if info.settlement_result is None:
                raise RuntimeError(f"运行没有待结算结果: {run_id}")
            return info.settlement_result, info.result_reported, tuple(info.pending_receipts)

    async def mark_result_reported(self, run_id: str) -> None:
        async with self._lock:
            self._require_run(run_id).result_reported = True

    async def mark_receipt_acked(self, run_id: str, receipt: str) -> None:
        async with self._lock:
            self._require_run(run_id).pending_receipts.discard(receipt)

    async def finish_settlement(self, run_id: str) -> bool:
        """结果已上报且没有待 ACK receipt 时原子清除运行状态。"""
        async with self._lock:
            info = self._require_run(run_id)
            if not info.result_reported or info.pending_receipts:
                return False
            self._runs.pop(run_id)
            return True

    async def release_settlement(self, run_id: str) -> None:
        """结算尝试失败后释放本地恢复权，但保留结果与 receipt。"""
        async with self._lock:
            info = self._runs.get(run_id)
            if info is not None:
                info.settlement_active = False

    async def has_pending_settlement(self, run_id: str) -> bool:
        async with self._lock:
            info = self._runs.get(run_id)
            return info is not None and info.settlement_result is not None

    def _require_run(self, run_id: str) -> RunInfo:
        info = self._runs.get(run_id)
        if info is None:
            raise RuntimeError(f"运行不存在: {run_id}")
        return info

    async def get(self, run_id: str) -> RunInfo | None:
        """获取运行信息"""
        async with self._lock:
            return self._runs.get(run_id)

    async def request_cancel(self, run_id: str) -> CancelRequest | None:
        """Atomically mark a non-terminal run and return an immutable snapshot."""
        async with self._lock:
            info = self._runs.get(run_id)
            if info is None:
                return None
            if info.state not in (RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED):
                info.data["cancel_requested"] = True
            return CancelRequest(
                run_id=info.run_id,
                task_id=info.task_id,
                state=info.state,
                receipt=info.receipt,
                queued_at=info.queued_at,
            )

    async def transition_if_not_cancel_requested(self, run_id: str, new_state: RunState) -> bool:
        """Transition only while the run has no concurrent cancellation request."""
        async with self._lock:
            info = self._runs.get(run_id)
            if info is None or info.data.get("cancel_requested"):
                return False
            return self._transition_locked(info, new_state)

    async def transition(self, run_id: str, new_state: RunState) -> bool:
        """状态转换"""
        async with self._lock:
            info = self._runs.get(run_id)
            if not info:
                logger.warning(f"运行不存在: {run_id}")
                return False
            return self._transition_locked(info, new_state)

    def _transition_locked(self, info: RunInfo, new_state: RunState) -> bool:
        valid_next = self.VALID_TRANSITIONS.get(info.state, set())
        if new_state not in valid_next:
            logger.warning(f"无效状态转换: {info.state} -> {new_state}")
            return False
        old_state = info.state
        info.state = new_state
        if new_state == RunState.RUNNING:
            info.started_at = datetime.now()
        elif new_state in (RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED):
            info.finished_at = datetime.now()
        logger.debug(f"状态转换: {info.run_id} {old_state} -> {new_state}")
        return True

    async def remove(self, run_id: str) -> RunInfo | None:
        """移除运行"""
        async with self._lock:
            return self._runs.pop(run_id, None)

    async def list_by_state(self, state: RunState) -> list[RunInfo]:
        """按状态列出运行"""
        async with self._lock:
            return [r for r in self._runs.values() if r.state == state]

    async def count_active(self) -> int:
        """统计活跃运行数"""
        async with self._lock:
            active_states = {RunState.QUEUED, RunState.PREPARING, RunState.RUNNING, RunState.CANCELLING}
            return sum(
                1 for run in self._runs.values() if run.state in active_states or run.settlement_result is not None
            )

    async def get_all(self) -> list[RunInfo]:
        """获取所有运行"""
        async with self._lock:
            return list(self._runs.values())
