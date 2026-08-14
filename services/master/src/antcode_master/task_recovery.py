"""Leader-owned recovery of interrupted task runs."""

from datetime import datetime

from antcode_core.domain.models import Task, TaskRun
from antcode_core.domain.models.enums import TaskStatus
from loguru import logger

from antcode_master.control.execution_parameters import RecoveryExecutionOptions
from antcode_master.task_persistence import CheckpointState, TaskPersistenceService


class TaskRecoveryService:
    def __init__(self) -> None:
        self.persistence = TaskPersistenceService()
        self._recovering = False

    async def recover_on_startup(self) -> dict[str, int]:
        if not _leader_allows_recovery():
            logger.info("非 Leader Master, 跳过启动恢复 (由当前 leader 执行)")
            return _empty_stats()
        if self._recovering:
            logger.warning("恢复任务已在进行中")
            return _empty_stats()
        self._recovering = True
        try:
            return await self._recover_all()
        finally:
            self._recovering = False

    async def _recover_all(self) -> dict[str, int]:
        logger.info("开始检查需要恢复的任务...")
        interrupted = await self.persistence.get_interrupted_tasks()
        logger.info(f"发现 {len(interrupted)} 个中断的任务")
        stats = _empty_stats()
        for checkpoint in interrupted:
            outcome = await self._recover_checkpoint(checkpoint)
            stats[outcome] += 1
        logger.info(
            "任务恢复完成: 成功 {}, 失败 {}, 跳过 {}",
            stats["recovered"],
            stats["failed"],
            stats["skipped"],
        )
        return stats

    async def _recover_checkpoint(self, checkpoint) -> str:
        if checkpoint.retry_count >= TaskPersistenceService.MAX_RETRY_ON_RECOVERY:
            await self._mark_task_failed(checkpoint, "任务恢复失败，重试次数超限")
            return "failed"
        return "recovered" if await self._recover_task(checkpoint) else "skipped"

    async def _recover_task(self, checkpoint) -> bool:
        task = await Task.get_or_none(id=checkpoint.task_id)
        if task is None:
            logger.warning(f"任务不存在: task_id={checkpoint.task_id}")
            return False
        checkpoint.state = CheckpointState.RECOVERED
        checkpoint.retry_count += 1
        # save_checkpoint 失败会抛 CheckpointPersistenceError，此处不再做布尔判断兜底
        await self.persistence.save_checkpoint(checkpoint)
        resume_data = _resume_data(checkpoint)
        from antcode_master.control.scheduler_loop import scheduler_service

        await scheduler_service.trigger_task(
            task.id,
            idempotency_key=f"recovery:{checkpoint.run_id}",
            recovery_options=RecoveryExecutionOptions(checkpoint.run_id, resume_data),
        )
        logger.info(
            "任务已恢复调度: run_id={}, progress={:.1%}, retry_count={}",
            checkpoint.run_id,
            checkpoint.progress,
            checkpoint.retry_count,
        )
        return True

    async def _mark_task_failed(self, checkpoint, error_message: str) -> None:
        await TaskRun.filter(run_id=checkpoint.run_id).update(
            status=TaskStatus.FAILED,
            error_message=error_message,
            end_time=datetime.now(),
        )
        await self.persistence.delete_checkpoint(checkpoint.run_id)

    async def recover_single_task(self, run_id: str) -> bool:
        checkpoint = await self.persistence.get_checkpoint(run_id)
        if checkpoint is None:
            logger.warning(f"未找到检查点: {run_id}")
            return False
        return await self._recover_task(checkpoint)


def _resume_data(checkpoint) -> dict:
    if not checkpoint.checkpoint_data and checkpoint.progress <= 0:
        return {}
    return {
        "_resume": True,
        "_checkpoint": checkpoint.checkpoint_data,
        "_progress": checkpoint.progress,
        "_last_log_offset": checkpoint.last_log_offset,
        "_previous_run_id": checkpoint.run_id,
    }


def _leader_allows_recovery() -> bool:
    try:
        from antcode_master.leader import leader_election
    except ImportError:
        return True
    return bool(leader_election.is_leader)


def _empty_stats() -> dict[str, int]:
    return {"recovered": 0, "failed": 0, "skipped": 0}


task_recovery_service = TaskRecoveryService()

__all__ = ["TaskRecoveryService", "task_recovery_service"]
