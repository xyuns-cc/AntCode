"""任务持久化与恢复服务

提供任务检查点持久化和故障恢复功能:
- TaskCheckpoint: 任务检查点数据结构
- TaskPersistenceService: 检查点持久化服务
- TaskRecoveryService: 任务恢复服务
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from enum import Enum

from loguru import logger

from antcode_core.infrastructure.cache import unified_cache


class CheckpointState(str, Enum):
    """检查点状态"""

    PENDING = "pending"
    RUNNING = "running"
    CHECKPOINTED = "checkpointed"
    FAILED = "failed"
    RECOVERED = "recovered"


@dataclass
class TaskCheckpoint:
    """任务检查点"""

    execution_id: str
    task_id: int
    task_public_id: str
    worker_id: str | None = None
    state: CheckpointState = CheckpointState.PENDING
    progress: float = 0.0
    checkpoint_data: dict = field(default_factory=dict)
    last_log_offset: int = 0
    started_at: datetime = None
    last_checkpoint_at: datetime = None
    retry_count: int = 0
    error_message: str = None

    def to_dict(self):
        """转换为字典"""
        data = asdict(self)
        if self.started_at:
            data["started_at"] = self.started_at.isoformat()
        if self.last_checkpoint_at:
            data["last_checkpoint_at"] = self.last_checkpoint_at.isoformat()
        data["state"] = self.state.value
        return data

    @classmethod
    def from_dict(cls, data):
        """从字典创建"""
        if data.get("started_at") and isinstance(data["started_at"], str):
            data["started_at"] = datetime.fromisoformat(data["started_at"])
        if data.get("last_checkpoint_at") and isinstance(data["last_checkpoint_at"], str):
            data["last_checkpoint_at"] = datetime.fromisoformat(data["last_checkpoint_at"])
        if data.get("state") and isinstance(data["state"], str):
            data["state"] = CheckpointState(data["state"])
        return cls(**data)


class TaskPersistenceService:
    """任务持久化服务"""

    CHECKPOINT_CACHE_PREFIX = "checkpoint:"
    CHECKPOINT_CACHE_TTL = 86400
    MAX_RETRY_ON_RECOVERY = 3
    INTERRUPTED_THRESHOLD_MINUTES = 2
    HEARTBEAT_INTERVAL_SECONDS = 30

    async def save_checkpoint(self, checkpoint):
        """保存任务检查点"""
        try:
            checkpoint.last_checkpoint_at = datetime.now()
            await self._save_to_db(checkpoint)

            cache_key = f"{self.CHECKPOINT_CACHE_PREFIX}{checkpoint.execution_id}"
            await unified_cache.set(cache_key, checkpoint.to_dict(), ttl=self.CHECKPOINT_CACHE_TTL)

            logger.debug(
                f"检查点已保存: execution_id={checkpoint.execution_id}, "
                f"progress={checkpoint.progress:.1%}"
            )
            return True

        except Exception as e:
            logger.error(f"保存检查点失败: {e}")
            return False

    async def _save_to_db(self, checkpoint):
        """保存检查点到数据库"""
        from antcode_core.domain.models import TaskRun

        try:
            execution = await TaskRun.get_or_none(execution_id=checkpoint.execution_id)
            if execution:
                result_data = execution.result_data or {}
                result_data["checkpoint"] = checkpoint.to_dict()
                execution.result_data = result_data
                await execution.save(update_fields=["result_data"])
        except Exception as e:
            logger.warning(f"保存检查点到数据库失败: {e}")

    async def get_checkpoint(self, execution_id):
        """获取任务检查点"""
        try:
            cache_key = f"{self.CHECKPOINT_CACHE_PREFIX}{execution_id}"
            data = await unified_cache.get(cache_key)
            if data:
                return TaskCheckpoint.from_dict(data)
        except Exception as e:
            logger.debug(f"从缓存读取检查点失败: {e}")

        try:
            from antcode_core.domain.models import TaskRun

            execution = await TaskRun.get_or_none(execution_id=execution_id)
            if execution and execution.result_data:
                checkpoint_data = execution.result_data.get("checkpoint")
                if checkpoint_data:
                    return TaskCheckpoint.from_dict(checkpoint_data)
        except Exception as e:
            logger.warning(f"从数据库读取检查点失败: {e}")

        return None

    async def delete_checkpoint(self, execution_id):
        """删除任务检查点"""
        try:
            cache_key = f"{self.CHECKPOINT_CACHE_PREFIX}{execution_id}"
            await unified_cache.delete(cache_key)
        except Exception as e:
            logger.debug(f"删除缓存检查点失败: {e}")

    async def update_heartbeat(self, execution_id):
        """更新任务心跳"""
        from antcode_core.domain.models import TaskRun

        try:
            updated = await TaskRun.filter(execution_id=execution_id).update(
                last_heartbeat=datetime.now()
            )
            return updated > 0
        except Exception as e:
            logger.debug(f"更新心跳失败: {e}")
            return False

    async def get_interrupted_tasks(self):
        """获取所有被中断的任务"""
        from tortoise.expressions import Q

        from antcode_core.domain.models import Task, TaskRun
        from antcode_core.domain.models.enums import TaskStatus

        try:
            cutoff = datetime.now() - timedelta(minutes=self.INTERRUPTED_THRESHOLD_MINUTES)

            interrupted_executions = (
                await TaskRun.filter(status=TaskStatus.RUNNING)
                .filter(
                    Q(last_heartbeat__lt=cutoff)
                    | Q(last_heartbeat__isnull=True, start_time__lt=cutoff)
                )
                .limit(100)
            )

            if not interrupted_executions:
                return []

            task_ids = [e.task_id for e in interrupted_executions]
            tasks = await Task.filter(id__in=task_ids)
            task_map = {t.id: t for t in tasks}

            orphan_executions = [e for e in interrupted_executions if e.task_id not in task_map]
            if orphan_executions:
                orphan_ids = [e.execution_id for e in orphan_executions]
                await TaskRun.filter(execution_id__in=orphan_ids).update(
                    status=TaskStatus.FAILED,
                    error_message="任务已被删除",
                    end_time=datetime.now(),
                )
                logger.info(f"已清理 {len(orphan_executions)} 条孤立的执行记录（任务已删除）")

            checkpoints = []
            for execution in interrupted_executions:
                task = task_map.get(execution.task_id)
                if not task:
                    continue

                checkpoint = None
                if execution.result_data and execution.result_data.get("checkpoint"):
                    try:
                        checkpoint = TaskCheckpoint.from_dict(execution.result_data["checkpoint"])
                        checkpoint.state = CheckpointState.CHECKPOINTED
                    except Exception:
                        pass

                if not checkpoint:
                    checkpoint = TaskCheckpoint(
                        execution_id=execution.execution_id,
                        task_id=execution.task_id,
                        task_public_id=task.public_id,
                        state=CheckpointState.CHECKPOINTED,
                        progress=0.0,
                        started_at=execution.start_time,
                    )

                checkpoints.append(checkpoint)

            return checkpoints

        except Exception as e:
            logger.error(f"获取中断任务失败: {e}")
            return []

    async def update_progress(self, execution_id, progress, checkpoint_data=None):
        """更新任务进度"""
        try:
            checkpoint = await self.get_checkpoint(execution_id)
            if checkpoint:
                checkpoint.progress = min(1.0, max(0.0, progress))
                if checkpoint_data:
                    checkpoint.checkpoint_data.update(checkpoint_data)
                await self.save_checkpoint(checkpoint)
        except Exception as e:
            logger.debug(f"更新进度失败: {e}")


class TaskRecoveryService:
    """任务恢复服务"""

    def __init__(self):
        self.persistence = TaskPersistenceService()
        self._recovering = False

    async def recover_on_startup(self):
        """Master 启动时恢复中断的任务"""
        if self._recovering:
            logger.warning("恢复任务已在进行中")
            return {"recovered": 0, "failed": 0, "skipped": 0}

        self._recovering = True
        stats = {"recovered": 0, "failed": 0, "skipped": 0}

        try:
            logger.info("开始检查需要恢复的任务...")

            interrupted = await self.persistence.get_interrupted_tasks()
            logger.info(f"发现 {len(interrupted)} 个中断的任务")

            for checkpoint in interrupted:
                try:
                    if checkpoint.retry_count >= TaskPersistenceService.MAX_RETRY_ON_RECOVERY:
                        logger.warning(
                            f"任务 {checkpoint.execution_id} 重试次数过多 "
                            f"({checkpoint.retry_count}次)，标记为失败"
                        )
                        await self._mark_task_failed(checkpoint, "任务恢复失败，重试次数超限")
                        stats["failed"] += 1
                        continue

                    success = await self._recover_task(checkpoint)
                    if success:
                        stats["recovered"] += 1
                    else:
                        stats["skipped"] += 1

                except Exception as e:
                    logger.error(f"恢复任务 {checkpoint.execution_id} 异常: {e}")
                    stats["failed"] += 1

            logger.info(
                f"任务恢复完成: 成功 {stats['recovered']}, "
                f"失败 {stats['failed']}, 跳过 {stats['skipped']}"
            )

        finally:
            self._recovering = False

        return stats

    async def _recover_task(self, checkpoint):
        """恢复单个任务"""
        try:
            from antcode_core.domain.models import Task, TaskRun
            from antcode_core.domain.models.enums import TaskStatus

            task = await Task.get_or_none(id=checkpoint.task_id)
            if not task:
                logger.warning(f"任务不存在: task_id={checkpoint.task_id}")
                return False

            checkpoint.state = CheckpointState.RECOVERED
            checkpoint.retry_count += 1
            await self.persistence.save_checkpoint(checkpoint)

            await TaskRun.filter(execution_id=checkpoint.execution_id).update(
                status=TaskStatus.FAILED,
                error_message="任务中断，已重新调度",
                end_time=datetime.now(),
            )

            resume_data = None
            if checkpoint.checkpoint_data or checkpoint.progress > 0:
                resume_data = {
                    "_resume": True,
                    "_checkpoint": checkpoint.checkpoint_data,
                    "_progress": checkpoint.progress,
                    "_last_log_offset": checkpoint.last_log_offset,
                    "_previous_execution_id": checkpoint.execution_id,
                }

            if resume_data:
                execution_params = task.execution_params or {}
                execution_params.update(resume_data)
                task.execution_params = execution_params
                await task.save(update_fields=["execution_params"])
                logger.debug(f"已注入断点续传数据到任务 {task.public_id}")

            from antcode_master.loops.scheduler_loop import scheduler_service

            await scheduler_service.trigger_task(task.id)

            logger.info(
                f"任务已恢复调度: execution_id={checkpoint.execution_id}, "
                f"进度={checkpoint.progress:.1%}, 重试次数={checkpoint.retry_count}, "
                f"断点续传={'是' if resume_data else '否'}"
            )

            return True

        except Exception as e:
            logger.error(f"恢复任务失败: {e}")
            return False

    async def _mark_task_failed(self, checkpoint, error_message):
        """标记任务为失败"""
        try:
            from antcode_core.domain.models import TaskRun
            from antcode_core.domain.models.enums import TaskStatus

            await TaskRun.filter(execution_id=checkpoint.execution_id).update(
                status=TaskStatus.FAILED,
                error_message=error_message,
                end_time=datetime.now(),
            )

            await self.persistence.delete_checkpoint(checkpoint.execution_id)

        except Exception as e:
            logger.error(f"标记任务失败异常: {e}")

    async def recover_single_task(self, execution_id):
        """手动恢复单个任务"""
        checkpoint = await self.persistence.get_checkpoint(execution_id)
        if not checkpoint:
            logger.warning(f"未找到检查点: {execution_id}")
            return False

        return await self._recover_task(checkpoint)


task_persistence_service = TaskPersistenceService()
task_recovery_service = TaskRecoveryService()
