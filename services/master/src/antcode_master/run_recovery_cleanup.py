"""把无法恢复的中断 run 收敛成终态，并给出**真实**的失败原因。

爬取批次 run 的 ``task_id`` 恒为 ``TASK_ID_ABSENT``（批次没有 Task 载体，见
``batch_dispatcher_service``），``scheduled_tasks`` 里永远不存在 id=0 的行。老实现
只判 ``task_id not in task_map``，于是**每一个**中断的批次 run 都被打上「任务已被
删除」——一句假消息，实际什么都没被删，排障时会把人引去查任务删除记录。

这与 ``10e44b7`` 修掉的 ``dispatch_authorization._load_task_run_owners`` 是同族缺陷
（``task_id=0`` 哨兵撞上「只认 Task 行」的代码），那次漏了这一处。两类 run 在这里
分开归因：真的丢了 Task 行的才叫「任务已被删除」，批次 run 按中断如实记录。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from antcode_core.domain.models.task_run import TASK_ID_ABSENT
from loguru import logger

ORPHAN_TASK_ERROR = "任务已被删除"
INTERRUPTED_BATCH_ERROR = "执行中断：Worker 心跳超时且批次执行无法恢复"


async def cleanup_unrecoverable_runs(interrupted_executions, task_map, *, TaskRun, TaskStatus) -> None:
    """按归因分两批收敛：真孤儿 vs 中断的批次 run。"""
    orphans = [
        run for run in interrupted_executions if int(run.task_id) != TASK_ID_ABSENT and run.task_id not in task_map
    ]
    batch_runs = [run for run in interrupted_executions if int(run.task_id) == TASK_ID_ABSENT]
    await _fail_runs(orphans, ORPHAN_TASK_ERROR, "孤立", TaskRun=TaskRun, TaskStatus=TaskStatus)
    await _fail_runs(batch_runs, INTERRUPTED_BATCH_ERROR, "中断批次", TaskRun=TaskRun, TaskStatus=TaskStatus)


async def _fail_runs(executions: list[Any], error_message: str, label: str, *, TaskRun, TaskStatus) -> None:
    if not executions:
        return
    await TaskRun.filter(run_id__in=[run.run_id for run in executions]).update(
        status=TaskStatus.FAILED,
        error_message=error_message,
        end_time=datetime.now(),
    )
    logger.info(f"已清理 {len(executions)} 条{label}的执行记录（{error_message}）")


__all__ = ["INTERRUPTED_BATCH_ERROR", "ORPHAN_TASK_ERROR", "cleanup_unrecoverable_runs"]
