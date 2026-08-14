"""Durable DATE/ONCE discovery and deterministic execution identity."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from antcode_core.domain.models.enums import DispatchStatus, ScheduleType, TaskStatus
from antcode_core.domain.models.task import Task
from antcode_core.domain.models.task_run import TaskRun
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

SCHEDULED_RUN_NAMESPACE = uuid.UUID("db1e5b15-55e5-4d4e-94f4-a90c1a44ea9a")
DUE_TASK_BATCH_SIZE = 100
ONE_TIME_SCHEDULE_TYPES = (ScheduleType.DATE, ScheduleType.ONCE)
RECOVERABLE_STATUSES = (TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.DISPATCHING)
DURABLE_SCHEDULE_JOB_ID = "__durable_one_time_schedule__"
DURABLE_SCHEDULE_INTERVAL_SECONDS = 30


def scheduled_fire_time(task: Task) -> datetime:
    fire_time = task.scheduled_time or task.created_at
    if fire_time is None:
        raise ValueError(f"一次性任务缺少持久时间锚点: task_id={task.id}")
    if fire_time.tzinfo is None:
        return fire_time.replace(tzinfo=UTC)
    return fire_time.astimezone(UTC)


def scheduled_run_id(task: Task) -> str:
    fire_time = scheduled_fire_time(task).isoformat(timespec="microseconds")
    return str(uuid.uuid5(SCHEDULED_RUN_NAMESPACE, f"{task.id}:{fire_time}"))


def is_recoverable_scheduled_run(execution) -> bool:
    return (
        execution.dispatch_status in (DispatchStatus.PENDING, DispatchStatus.DISPATCHING)
        and execution.runtime_status is None
        and execution.status in RECOVERABLE_STATUSES
    )


def create_task_trigger(task: Task):
    if task.schedule_type == ScheduleType.CRON:
        return CronTrigger.from_crontab(task.cron_expression)
    if task.schedule_type == ScheduleType.INTERVAL:
        return IntervalTrigger(seconds=task.interval_seconds)
    if task.schedule_type == ScheduleType.DATE:
        return DateTrigger(run_date=task.scheduled_time)
    if task.schedule_type == ScheduleType.ONCE:
        return DateTrigger(run_date=task.scheduled_time or datetime.now())
    raise ValueError(f"不支持的调度类型: {task.schedule_type}")


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
        run_id = scheduled_run_id(task)
        execution = await TaskRun.get_or_none(run_id=run_id)
        accepted = await self._accept(task, execution, token)
        if accepted is not None:
            await complete_one_time_schedule(task.id, run_id, token)

    async def _accept(self, task: Task, execution: Any, token: int) -> str | None:
        run_id = scheduled_run_id(task)
        if execution is None:
            return await self._service._execute_task(
                task.id,
                fixed_run_id=run_id,
                scheduler_fencing_token=token,
            )
        if not is_recoverable_scheduled_run(execution):
            return run_id
        owned = await take_over_pre_dispatch_run(run_id, token)
        if owned is None:
            raise RuntimeError(f"一次性 run 无法接管: run_id={run_id}")
        return await resume_existing_run(self._service, task.id, owned)


__all__ = [
    "DUE_TASK_BATCH_SIZE",
    "DurableScheduleRunner",
    "ONE_TIME_SCHEDULE_TYPES",
    "create_task_trigger",
    "is_recoverable_scheduled_run",
    "load_due_tasks_page",
    "scheduled_fire_time",
    "scheduled_run_id",
]
