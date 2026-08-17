"""Outbox ``task_trigger`` 的耐久、重放安全执行路径。"""

from __future__ import annotations

from typing import Any

from antcode_core.application.services.scheduler.trigger_identity import (
    TRIGGER_RUN_NAMESPACE,
    dispatch_run_id,
    trigger_run_id,
)
from antcode_core.domain.models import Task, TaskRun
from loguru import logger

from antcode_master.control.durable_schedule import is_recoverable_scheduled_run
from antcode_master.control.retry_dispatch_recovery import resume_existing_run
from antcode_master.control.scheduler_authority import (
    SchedulerAuthorityLost,
    take_over_pre_dispatch_run,
)
from antcode_master.leader import require_fencing_token


class TriggerDeferred(RuntimeError):
    """任务当前不可接纳触发；Outbox/PEL 必须保留并稍后重试。"""


def target_run_id(task: Any, idempotency_key: str, recovery_options: Any | None) -> str:
    """本次 ``task_trigger`` 应当落在的 run 身份。

    恢复触发创建的是被中断 run 的**后继**执行，必须保留自己的 outbox 身份，
    否则会折叠回它正要接替的那个 run；普通触发走 ``dispatch_run_id``，与耐久
    扫描共用一次性任务的调度槽位身份。
    """
    if recovery_options is not None:
        return trigger_run_id(task.id, idempotency_key)
    return dispatch_run_id(task, idempotency_key)


async def execute_idempotent_trigger(
    execute_func: Any,
    resume_func: Any,
    task_id: Any,
    *,
    idempotency_key: str,
    recovery_options: Any | None = None,
) -> str:
    task = await Task.get_or_none(id=task_id)
    if task is None:
        raise LookupError(f"task_trigger 目标不存在: task_id={task_id}")
    run_id = target_run_id(task, idempotency_key, recovery_options)
    existing = await TaskRun.get_or_none(run_id=run_id)
    if existing is not None:
        logger.info(f"task_trigger 命中已存在 run，检查恢复: task_id={task_id} run_id={run_id}")
        return await resume_func(task_id, existing)
    if not task.is_active:
        raise TriggerDeferred(f"task_trigger 目标未激活: task_id={task_id}")
    execute_kwargs: dict[str, Any] = {"fixed_run_id": run_id}
    if recovery_options is not None:
        execute_kwargs["recovery_options"] = recovery_options
    created_run_id = await execute_func(task_id, **execute_kwargs)
    if created_run_id is None:
        raise TriggerDeferred(f"task_trigger 当前不可接纳: task_id={task_id}")
    if created_run_id != run_id:
        raise RuntimeError(f"task_trigger run_id 漂移: expected={run_id} actual={created_run_id}")
    logger.info(f"task_trigger 耐久 run 已创建: task_id={task_id} run_id={run_id}")
    return run_id


async def resume_idempotent_trigger(service: Any, task_id: int, execution: Any) -> str:
    if not is_recoverable_scheduled_run(execution):
        return execution.run_id
    token = await require_fencing_token()
    owned = await take_over_pre_dispatch_run(execution.run_id, token)
    if owned is None:
        raise SchedulerAuthorityLost(f"trigger run 无法由当前任期接管: run_id={execution.run_id}")
    return await resume_existing_run(service, task_id, owned)


__all__ = [
    "TRIGGER_RUN_NAMESPACE",
    "TriggerDeferred",
    "execute_idempotent_trigger",
    "resume_idempotent_trigger",
    "target_run_id",
]
