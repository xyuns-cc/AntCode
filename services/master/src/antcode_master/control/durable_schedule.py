"""Durable DATE/ONCE discovery and deterministic execution identity."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from antcode_core.application.services.scheduler.trigger_identity import (
    ONE_TIME_SCHEDULE_TYPES,
    SCHEDULED_RUN_NAMESPACE,
    scheduled_fire_time,
    scheduled_run_id,
)
from antcode_core.common.config import settings
from antcode_core.domain.models.enums import DispatchStatus, ScheduleType, TaskStatus
from antcode_core.domain.models.task import Task
from antcode_core.domain.models.task_run import TaskRun
from antcode_core.domain.models.task_status_sets import TASK_RUN_ACTIVE_STATUSES
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger
from tortoise.expressions import Q

from antcode_master.control.retry_dispatch_recovery import resume_existing_run
from antcode_master.control.scheduler_authority import (
    SchedulerAuthorityLost,
    complete_one_time_schedule,
    take_over_pre_dispatch_run,
)
from antcode_master.leader import require_fencing_token

DUE_TASK_BATCH_SIZE = 100
RECOVERABLE_STATUSES = (TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.DISPATCHING)
DURABLE_SCHEDULE_JOB_ID = "__durable_one_time_schedule__"
DURABLE_SCHEDULE_INTERVAL_SECONDS = 30


def is_recoverable_scheduled_run(execution) -> bool:
    return (
        execution.dispatch_status in (DispatchStatus.PENDING, DispatchStatus.DISPATCHING)
        and execution.runtime_status is None
        and execution.status in RECOVERABLE_STATUSES
    )


def create_task_trigger(task: Task):
    """按 ``SCHEDULER_TIMEZONE`` 构造触发器。

    APScheduler 只在 ``add_job`` 自己构造 trigger 时才套用 scheduler 的默认
    时区；传入已构造好的 trigger 对象时，trigger 保留自身时区，而
    ``CronTrigger.from_crontab()`` 不带 ``timezone`` 会退到**系统本地时区**
    （容器里没设 TZ，即 UTC）。结果是 ``SCHEDULER_TIMEZONE=Asia/Shanghai``
    对 cron 完全失效：``0 3 * * *`` 实际在 03:00 UTC / 北京时间 11:00 触发。
    """
    timezone = settings.SCHEDULER_TIMEZONE
    if task.schedule_type == ScheduleType.CRON:
        return CronTrigger.from_crontab(task.cron_expression, timezone=timezone)
    if task.schedule_type == ScheduleType.INTERVAL:
        return IntervalTrigger(seconds=task.interval_seconds, timezone=timezone)
    if task.schedule_type == ScheduleType.DATE:
        return DateTrigger(run_date=task.scheduled_time, timezone=timezone)
    if task.schedule_type == ScheduleType.ONCE:
        return DateTrigger(run_date=task.scheduled_time or datetime.now(UTC), timezone=timezone)
    raise ValueError(f"不支持的调度类型: {task.schedule_type}")


async def one_time_schedule_is_fulfilled(task_id: int) -> bool:
    """一次性调度是否已彻底兑现：既无活跃 run，也无待兑现的重试意图。

    调度槽位在此之前不得关闭 —— 关闭即 ``is_active=False``，而 retry claim
    以 ``is_active`` 判定目标有效性，提前关闭会让首次执行失败后的重试全部
    被判为 ``RetryTargetInvalidError`` 直接丢弃。
    """
    unfulfilled = Q(status__in=list(TASK_RUN_ACTIVE_STATUSES)) | Q(next_retry_at__isnull=False)
    return not await TaskRun.filter(unfulfilled, task_id=task_id).exists()


async def load_due_tasks_page(now: datetime, after_id: int = 0) -> list[Task]:
    due = Q(schedule_type=ScheduleType.DATE, scheduled_time__lte=now)
    due |= Q(schedule_type=ScheduleType.ONCE, scheduled_time__lte=now)
    due |= Q(schedule_type=ScheduleType.ONCE, scheduled_time__isnull=True)
    return await Task.filter(due, id__gt=after_id, is_active=True).order_by("id").limit(DUE_TASK_BATCH_SIZE)


class DurableScheduleRunner:
    """Database-backed discovery and recovery for one-time schedules."""

    def __init__(self, service: Any) -> None:
        self._service = service
        self._lock = asyncio.Lock()

    async def register(self, task: Task) -> None:
        job = self._service.scheduler.get_job(str(task.id))
        if job is not None:
            self._service.scheduler.remove_job(str(task.id))
        fire_time = scheduled_fire_time(task)
        await Task.filter(id=task.id, is_active=True).update(next_run_time=fire_time)
        logger.info(f"一次性任务进入耐久扫描: task_id={task.id} fire_time={fire_time.isoformat()}")

    def install_job(self) -> None:
        self._service.scheduler.add_job(
            self.recover_due,
            trigger=IntervalTrigger(seconds=DURABLE_SCHEDULE_INTERVAL_SECONDS),
            id=DURABLE_SCHEDULE_JOB_ID,
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )

    async def recover_due(self) -> None:
        if self._lock.locked():
            return
        async with self._lock:
            token = await require_fencing_token()
            await self._scan(token)
            await self._recover_interrupted_runs()

    @staticmethod
    async def _recover_interrupted_runs() -> None:
        from antcode_master.task_recovery import task_recovery_service

        stats = await task_recovery_service.recover_on_startup()
        logger.info(
            "Leader 任期中断恢复扫描完成: recovered={} failed={} skipped={}",
            stats["recovered"],
            stats["failed"],
            stats["skipped"],
        )

    async def _scan(self, token: int) -> None:
        cursor = 0
        failures: list[int] = []
        now = datetime.now(UTC)
        while True:
            tasks = await load_due_tasks_page(now, cursor)
            if not tasks:
                break
            failures.extend(await self._execute_page(tasks, token))
            cursor = tasks[-1].id
            if len(tasks) < DUE_TASK_BATCH_SIZE:
                break
        if failures:
            raise RuntimeError(f"耐久一次性任务恢复失败: task_ids={failures}")

    async def _execute_page(self, tasks: list[Task], token: int) -> list[int]:
        failures: list[int] = []
        for task in tasks:
            try:
                await self.execute_task(task, token)
            except SchedulerAuthorityLost:
                raise
            except Exception:
                logger.exception(f"耐久一次性任务恢复失败: task_id={task.id}")
                failures.append(task.id)
        return failures

    async def execute_task(self, task: Task, token: int) -> None:
        """把一次性任务的调度槽位推进一步。

        槽位身份是 ``scheduled_run_id``，手动触发（``trigger_idempotency``）
        取的是同一个身份，因此"槽位已被占用"对两条派发路径都可见：先到者建
        run，后到者只能观察，绝不会各建一个 run 把任务跑两次。
        """
        run_id = scheduled_run_id(task)
        execution = await TaskRun.get_or_none(run_id=run_id)
        if execution is None or is_recoverable_scheduled_run(execution):
            await self._accept(task, execution, run_id=run_id, token=token)
            return
        if await one_time_schedule_is_fulfilled(task.id):
            await complete_one_time_schedule(task.id, run_id, token)

    async def _accept(self, task: Task, execution: Any, *, run_id: str, token: int) -> None:
        if execution is None:
            await self._service._execute_task(
                task.id,
                fixed_run_id=run_id,
                scheduler_fencing_token=token,
            )
            return
        owned = await take_over_pre_dispatch_run(run_id, token)
        if owned is None:
            raise RuntimeError(f"一次性 run 无法接管: run_id={run_id}")
        await resume_existing_run(self._service, task.id, owned)


__all__ = [
    "DUE_TASK_BATCH_SIZE",
    "ONE_TIME_SCHEDULE_TYPES",
    "SCHEDULED_RUN_NAMESPACE",
    "DurableScheduleRunner",
    "create_task_trigger",
    "is_recoverable_scheduled_run",
    "load_due_tasks_page",
    "one_time_schedule_is_fulfilled",
    "scheduled_fire_time",
    "scheduled_run_id",
]
