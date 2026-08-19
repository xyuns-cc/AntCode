"""周期任务注册进调度器时就必须把下次触发时间落库。

走查实测：新建一个 ``0 3 * * *`` 的 cron 任务，Master 日志确认
「任务 X 已添加到调度器」，但 ``tasks`` 表的 ``next_run_time`` 一直是 NULL，
任务详情页显示「下次运行时间：无计划」。原因是只有 ``_finalize_stats``
（一次执行结束之后）才写这个字段——日级 cron 意味着最长一整天读不到。
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.domain.models.enums import ScheduleType
from antcode_master.control import scheduler_next_run
from antcode_master.control.scheduler_loop import SchedulerService

NEXT_FIRE = datetime(2026, 8, 20, 3, 0, tzinfo=timezone(timedelta(hours=8)))


class _FakeJob:
    def __init__(self, next_run_time):
        self.next_run_time = next_run_time


class _FakeScheduler:
    """只实现 add_task 会用到的两个方法。"""

    def __init__(self, next_run_time=NEXT_FIRE):
        self.added: list[dict] = []
        self._job = _FakeJob(next_run_time)

    def add_job(self, **kwargs):
        self.added.append(kwargs)

    def get_job(self, _job_id):
        return self._job


def _cron_task() -> SimpleNamespace:
    return SimpleNamespace(
        id=630,
        name="cron走查任务",
        schedule_type=ScheduleType.CRON,
        cron_expression="0 3 * * *",
        interval_seconds=None,
        scheduled_time=None,
        max_instances=1,
    )


@pytest.mark.asyncio
async def test_add_task_persists_next_run_time(monkeypatch) -> None:
    service = SchedulerService()
    scheduler = _FakeScheduler()
    monkeypatch.setattr(service, "scheduler", scheduler, raising=False)
    persist = AsyncMock()
    monkeypatch.setattr("antcode_master.control.scheduler_loop.persist_next_run_time", persist)

    task = _cron_task()
    await service.add_task(task)

    assert scheduler.added, "任务应已注册进调度器"
    # 判据：注册之后立刻落库，而不是等首次执行结束。
    persist.assert_awaited_once_with(scheduler, task.id)


@pytest.mark.asyncio
async def test_persist_next_run_time_writes_scheduler_value(monkeypatch) -> None:
    updates: list[dict] = []

    class _FakeQuerySet:
        async def update(self, **kwargs):
            updates.append(kwargs)

    monkeypatch.setattr(
        scheduler_next_run,
        "Task",
        SimpleNamespace(filter=lambda **_kwargs: _FakeQuerySet()),
    )

    await scheduler_next_run.persist_next_run_time(_FakeScheduler(), 630)

    assert updates == [{"next_run_time": NEXT_FIRE}]


@pytest.mark.asyncio
async def test_persist_next_run_time_clears_when_job_has_no_fire_time(monkeypatch) -> None:
    updates: list[dict] = []

    class _FakeQuerySet:
        async def update(self, **kwargs):
            updates.append(kwargs)

    monkeypatch.setattr(
        scheduler_next_run,
        "Task",
        SimpleNamespace(filter=lambda **_kwargs: _FakeQuerySet()),
    )

    await scheduler_next_run.persist_next_run_time(_FakeScheduler(next_run_time=None), 630)

    assert updates == [{"next_run_time": None}]
