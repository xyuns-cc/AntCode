"""Task-scoped scheduler outbox event dispatch."""

from __future__ import annotations

from loguru import logger

from antcode_master.control.manual_retry_lineage import attach_manual_retry_lineage


async def dispatch_task_event(data: dict, event_type: str) -> bool:
    if event_type not in {"task_trigger", "task_changed"}:
        return False
    task_id = _task_id(data, event_type)
    if task_id is None:
        return True
    if event_type == "task_changed":
        from antcode_master.control.task_change_handler import handle_task_changed

        await handle_task_changed(task_id)
        return True
    await _trigger_task(data, task_id)
    return True


def _task_id(data: dict, event_type: str) -> int | None:
    raw = data.get("task_id")
    if not raw:
        logger.warning(f"调度事件缺少 task_id: {event_type}")
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning(f"调度事件 task_id 无效: {raw}")
        return None


async def _trigger_task(data: dict, task_id: int) -> None:
    from antcode_master.control.scheduler_loop import scheduler_service

    outbox_id = str(data.get("outbox_id") or "") or None
    target_run_id = await scheduler_service.trigger_task(task_id, idempotency_key=outbox_id)
    source_run_id = str(data.get("manual_retry_source_run_id") or "")
    if not source_run_id:
        return
    if not isinstance(target_run_id, str) or not target_run_id:
        raise RuntimeError(f"manual retry 未创建耐久 target run: task_id={task_id}")
    await attach_manual_retry_lineage(target_run_id, source_run_id, task_id)


__all__ = ["dispatch_task_event"]
