"""派发之后回看一次 run：它的状态是什么、日志是什么。

从 ``worker_dispatcher`` 拆出来，是因为这两件事**根本不与 Worker 通信**——状态取自
``task_runs`` 表、日志取自 ``distributed_log_service``。它们原先叫 ``*_from_worker``
并带一个从未被读过的 ``worker`` 形参，读代码的人会以为 Master 会去问 Worker。数据流向
也相反：派发是往外写，这里是往回读，失效只会得到"查不到"（None / 空列表），永远不会
影响任何一次派发的成败。
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from antcode_core.common.error_messages import normalize_persisted_error_message

# TaskRun.public_id 是去掉连字符的 uuid4，长度恒为 32；比它长的一定是 run_id 本身。
PUBLIC_ID_LENGTH = 32
DEFAULT_LOG_TAIL = 100

# 接口对外的 output/error 与日志服务内部的 stdout/stderr 是两套命名。
_LOG_TYPE_ALIASES = {"output": "stdout", "error": "stderr"}


async def get_run_status(task_id: Any) -> dict[str, Any] | None:
    """按 run_id（或 public_id）读回一次执行的状态。"""
    try:
        from antcode_core.domain.models.task_run import TaskRun

        task_id_str = str(task_id)
        execution = await TaskRun.get_or_none(run_id=task_id_str)
        if not execution and len(task_id_str) <= PUBLIC_ID_LENGTH:
            execution = await TaskRun.get_or_none(public_id=task_id_str)
        if not execution:
            return None

        return {
            "run_id": execution.run_id,
            "status": execution.status,
            "start_time": execution.start_time.isoformat() if execution.start_time else None,
            "end_time": execution.end_time.isoformat() if execution.end_time else None,
            "exit_code": execution.exit_code,
            "error_message": normalize_persisted_error_message(execution.error_message),
        }
    except Exception:
        logger.exception("获取任务状态失败")
        return None


async def get_run_logs(task_id: Any, *, log_type: str = "output", tail: int = DEFAULT_LOG_TAIL) -> list:
    """按 run_id 读回日志；``output``/``error`` 会翻成日志服务的 ``stdout``/``stderr``。"""
    try:
        from antcode_core.application.services.workers.distributed_log_service import distributed_log_service

        return await distributed_log_service.get_logs(
            run_id=str(task_id),
            log_type=_LOG_TYPE_ALIASES.get(log_type, log_type),
            tail=tail,
        )
    except Exception:
        logger.exception("获取任务日志失败")
        return []


__all__ = ["DEFAULT_LOG_TAIL", "get_run_logs", "get_run_status"]
