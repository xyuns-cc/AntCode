"""Apply durable task-change events to the active scheduler."""

from antcode_core.domain.models.task import Task

from antcode_master.control.retry_cleanup import cleanup_deleted_task_retries


async def handle_task_changed(task_id: int) -> None:
    from antcode_master.control.scheduler_loop import scheduler_service

    task = await Task.get_or_none(id=task_id)
    if task is None:
        await cleanup_deleted_task_retries(task_id)
    if task is None or not task.is_active:
        await scheduler_service.remove_task(task_id)
        return
    await scheduler_service.add_task(task)


__all__ = ["handle_task_changed"]
