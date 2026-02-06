"""
任务状态管理

Requirements: 4.3
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from loguru import logger


class RunState(str, Enum):
    """运行状态"""
    QUEUED = "queued"           # 在本地队列中
    PREPARING = "preparing"     # 准备运行时
    RUNNING = "running"         # 执行中
    CANCELLING = "cancelling"   # 取消中
    COMPLETED = "completed"     # 已完成
    FAILED = "failed"           # 失败
    CANCELLED = "cancelled"     # 已取消


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
        """添加新运行"""
        async with self._lock:
            if run_id in self._runs:
                return self._runs[run_id]

            info = RunInfo(
                run_id=run_id,
                task_id=task_id,
                receipt=receipt,
                queued_at=datetime.now(),
            )
            self._runs[run_id] = info
            logger.debug(f"添加运行: {run_id}")
            return info

    async def get(self, run_id: str) -> RunInfo | None:
        """获取运行信息"""
        async with self._lock:
            return self._runs.get(run_id)

    async def transition(self, run_id: str, new_state: RunState) -> bool:
        """状态转换"""
        async with self._lock:
            info = self._runs.get(run_id)
            if not info:
                logger.warning(f"运行不存在: {run_id}")
                return False

            valid_next = self.VALID_TRANSITIONS.get(info.state, set())
            if new_state not in valid_next:
                logger.warning(f"无效状态转换: {info.state} -> {new_state}")
                return False

            old_state = info.state
            info.state = new_state

            # 更新时间戳
            if new_state == RunState.RUNNING:
                info.started_at = datetime.now()
            elif new_state in (RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED):
                info.finished_at = datetime.now()

            logger.debug(f"状态转换: {run_id} {old_state} -> {new_state}")
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
            return sum(1 for r in self._runs.values() if r.state in active_states)

    async def get_all(self) -> list[RunInfo]:
        """获取所有运行"""
        async with self._lock:
            return list(self._runs.values())
