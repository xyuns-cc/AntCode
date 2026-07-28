"""SSE 日志流访问校验、会话校验与状态帧策略。"""

from __future__ import annotations

from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any

from antcode_core.application.services.projects.relation_service import relation_service
from antcode_core.domain.models import User, UserSession
from antcode_core.domain.models.task_run import TaskRun
from fastapi import HTTPException

from antcode_web_api.streams.sse import build_run_status_message

TERMINAL_STATUSES = frozenset({"success", "failed", "timeout", "cancelled", "skipped", "rejected"})
STATUS_MESSAGES = MappingProxyType(
    {
        "running": "任务正在执行中",
        "success": "任务执行成功",
        "failed": "任务执行失败",
        "queued": "任务排队中",
    }
)


async def verify_execution_access(run_id: str, user: User) -> TaskRun:
    """Fixed-cost lookup without distinguishing a missing run from a foreign run."""
    execution = await TaskRun.get_or_none(run_id=run_id)
    task_id = execution.task_id if execution is not None else 0
    task = await relation_service.get_task_by_id(task_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="执行记录不存在或无权访问")
    if user.is_admin:
        return execution
    if task is None or task.user_id != user.id:
        raise HTTPException(status_code=404, detail="执行记录不存在或无权访问")
    return execution


def build_current_status_message(run_id: str, execution: TaskRun) -> dict[str, Any]:
    """连接建立即刻推送的当前执行状态帧。"""
    status = execution.status.value if execution.status else "queued"
    return build_run_status_message(
        run_id=run_id,
        status=status,
        progress=100.0 if status in TERMINAL_STATUSES else None,
        message=STATUS_MESSAGES.get(status, f"当前状态: {status}"),
    )


async def load_current_status_message(run_id: str) -> dict[str, Any] | None:
    """PG 状态兜底：返回当前状态帧（不限终态）。

    P2 §4.2: 此前只兜底终态，broker 丢失 running 事件后页面可长期停留在
    旧状态。是否需要下发（与流内已知状态比较）由 active 层判断。
    """
    execution = await TaskRun.get_or_none(run_id=run_id)
    if execution is None:
        return None
    return build_current_status_message(run_id, execution)


async def session_still_valid(user_id: int, session_jti: str) -> bool:
    """周期会话重校验；数据库故障上抛，由流显式 fail-closed。"""
    now = datetime.now(UTC)
    session = await UserSession.filter(
        jti=session_jti,
        user_id=user_id,
        revoked_at__isnull=True,
        expires_at__gt=now,
    ).first()
    if session is None or not session_not_expired(session):
        return False
    user = await User.get_or_none(id=user_id)
    return user is not None and getattr(user, "is_active", True)


async def execution_access_still_valid(run_id: str, user_id: int) -> bool:
    """Re-evaluate current run ownership and current admin role."""
    user = await User.get_or_none(id=user_id)
    if user is None or not getattr(user, "is_active", True):
        return False
    execution = await TaskRun.get_or_none(run_id=run_id)
    task_id = execution.task_id if execution is not None else 0
    task = await relation_service.get_task_by_id(task_id)
    if execution is None:
        return False
    if user.is_admin:
        return True
    return task is not None and task.user_id == user.id


def session_not_expired(session: UserSession) -> bool:
    """按 UTC 判断服务端会话有效期；旧库 naive 时间按 UTC 解释。"""
    expires_at = getattr(session, "expires_at", None)
    if not isinstance(expires_at, datetime):
        return False
    expires_at_utc = expires_at.replace(tzinfo=UTC) if expires_at.tzinfo is None else expires_at.astimezone(UTC)
    return expires_at_utc > datetime.now(UTC)


__all__ = [
    "build_current_status_message",
    "execution_access_still_valid",
    "load_current_status_message",
    "session_not_expired",
    "session_still_valid",
    "verify_execution_access",
]
