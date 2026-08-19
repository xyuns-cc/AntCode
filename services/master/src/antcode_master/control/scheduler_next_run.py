"""Persist the scheduler-computed next fire time onto the ``Task`` row."""

from __future__ import annotations

from antcode_core.domain.models.task import Task


def next_run_time(scheduler, task_id):
    """读取 APScheduler 作业已算好的下次触发时间。"""
    job = scheduler.get_job(str(task_id))
    if job and job.next_run_time:
        return job.next_run_time
    return None


async def persist_next_run_time(scheduler, task_id) -> None:
    """把调度器算出的下次触发时间落库。

    此前只有 ``_finalize_stats``（一次执行结束之后）会写 ``next_run_time``。
    新建、重新启用、以及 Master 重启后重新加载的周期任务，在首次执行完成前
    DB 里始终是 NULL，任务详情页因此长期显示「下次运行时间：无计划」——
    对日级 cron 意味着最长一整个周期都读不到下次触发时间。
    """
    await Task.filter(id=task_id).update(next_run_time=next_run_time(scheduler, task_id))


__all__ = ["next_run_time", "persist_next_run_time"]
