"""P1-DB-01: outbox ``task_trigger`` 的重放安全触发路径。

outbox 消费是 at-least-once（执行后、标 consumed 前崩溃会重放）。以
outbox_id 派生确定性 run_id 与 job id：
- run 已存在 → 重放，直接返回；
- 未存在 → 挂确定性 id 的一次性作业（``replace_existing`` 折叠"作业
  已挂、consumed 未标"窗口的重放），作业执行时以确定性 run_id 创建
  run（``TaskRun.run_id`` 唯一约束兜底）。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from antcode_core.domain.models import TaskRun
from apscheduler.triggers.date import DateTrigger
from loguru import logger

# outbox task_trigger 的确定性 run_id 命名空间（重放去重）。
TRIGGER_RUN_NAMESPACE = uuid.UUID("5c1a5fd4-9a70-4a53-9d18-58f4d13ae0c2")


async def schedule_idempotent_trigger(
    scheduler: Any,
    execute_func: Any,
    task_id: Any,
    *,
    idempotency_key: str,
) -> None:
    run_id = str(uuid.uuid5(TRIGGER_RUN_NAMESPACE, f"{task_id}:{idempotency_key}"))
    if await TaskRun.filter(run_id=run_id).exists():
        logger.info(f"task_trigger 重放命中已存在 run，跳过: task_id={task_id} run_id={run_id}")
        return
    run_date = (
        datetime.now(scheduler.timezone) if hasattr(scheduler, "timezone") and scheduler.timezone else datetime.now(UTC)
    )
    scheduler.add_job(
        func=execute_func,
        trigger=DateTrigger(run_date=run_date),
        id=f"{task_id}_outbox_{idempotency_key}",
        kwargs={"task_id": task_id, "fixed_run_id": run_id},
        replace_existing=True,
    )
    logger.info(f"task_trigger 幂等作业已挂: task_id={task_id} run_id={run_id}")


__all__ = ["TRIGGER_RUN_NAMESPACE", "schedule_idempotent_trigger"]
