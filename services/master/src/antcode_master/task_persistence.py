"""任务持久化与恢复服务

提供任务检查点持久化和故障恢复功能:
- TaskCheckpoint: 任务检查点数据结构
- TaskPersistenceService: 检查点持久化服务
- TaskRecoveryService: 任务恢复服务
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from antcode_core.infrastructure.cache import unified_cache
from loguru import logger


class CheckpointState(StrEnum):
    """检查点状态"""

    PENDING = "pending"
    RUNNING = "running"
    CHECKPOINTED = "checkpointed"
    FAILED = "failed"
    RECOVERED = "recovered"


@dataclass
class TaskCheckpoint:
    """任务检查点"""

    run_id: str
    task_id: int
    task_public_id: str
    worker_id: str | None = None
    state: CheckpointState = CheckpointState.PENDING
    progress: float = 0.0
    checkpoint_data: dict = field(default_factory=dict)
    last_log_offset: int = 0
    started_at: datetime | None = None
    last_checkpoint_at: datetime | None = None
    retry_count: int = 0
    error_message: str | None = None

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
        payload = dict(data)
        if payload.get("started_at") and isinstance(payload["started_at"], str):
            payload["started_at"] = datetime.fromisoformat(payload["started_at"])
        if payload.get("last_checkpoint_at") and isinstance(payload["last_checkpoint_at"], str):
            payload["last_checkpoint_at"] = datetime.fromisoformat(payload["last_checkpoint_at"])
        if payload.get("state") and isinstance(payload["state"], str):
            payload["state"] = CheckpointState(payload["state"])
        return cls(**payload)


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

            cache_key = f"{self.CHECKPOINT_CACHE_PREFIX}{checkpoint.run_id}"
            await unified_cache.set(cache_key, checkpoint.to_dict(), ttl=self.CHECKPOINT_CACHE_TTL)

            logger.debug(f"检查点已保存: run_id={checkpoint.run_id}, progress={checkpoint.progress:.1%}")
            return True

        except Exception as e:
            logger.error(f"保存检查点失败: {e}")
            return False

    async def _save_to_db(self, checkpoint):
        """保存检查点到数据库"""
        from antcode_core.domain.models import TaskRun

        try:
            execution = await TaskRun.get_or_none(run_id=checkpoint.run_id)
            if execution:
                result_data = execution.result_data or {}
                result_data["checkpoint"] = checkpoint.to_dict()
                execution.result_data = result_data
                await execution.save(update_fields=["result_data"])
        except Exception as e:
            logger.warning(f"保存检查点到数据库失败: {e}")

    async def get_checkpoint(self, run_id):
        """获取任务检查点"""
        try:
            cache_key = f"{self.CHECKPOINT_CACHE_PREFIX}{run_id}"
            data = await unified_cache.get(cache_key)
            if data:
                return TaskCheckpoint.from_dict(data)
        except Exception as e:
            logger.debug(f"从缓存读取检查点失败: {e}")

        try:
            from antcode_core.domain.models import TaskRun

            execution = await TaskRun.get_or_none(run_id=run_id)
            if execution and execution.result_data:
                checkpoint_data = execution.result_data.get("checkpoint")
                if checkpoint_data:
                    return TaskCheckpoint.from_dict(checkpoint_data)
        except Exception as e:
            logger.warning(f"从数据库读取检查点失败: {e}")

        return None

    async def delete_checkpoint(self, run_id):
        """删除任务检查点"""
        try:
            cache_key = f"{self.CHECKPOINT_CACHE_PREFIX}{run_id}"
            await unified_cache.delete(cache_key)
        except Exception as e:
            logger.debug(f"删除缓存检查点失败: {e}")

    async def update_heartbeat(self, run_id):
        """更新任务心跳"""
        from antcode_core.domain.models import TaskRun

        try:
            updated = await TaskRun.filter(run_id=run_id).update(last_heartbeat=datetime.now())
            return updated > 0
        except Exception as e:
            logger.debug(f"更新心跳失败: {e}")
            return False

    async def get_interrupted_tasks(self):
        """获取所有被中断的任务; P1-FN-13: 非预期异常向上抛让 startup fatal。"""
        from antcode_core.domain.models import Task, TaskRun, Worker
        from antcode_core.domain.models.enums import TaskStatus
        from tortoise.expressions import Q

        cutoff = datetime.now() - timedelta(minutes=self.INTERRUPTED_THRESHOLD_MINUTES)
        interrupted_executions = (
            await TaskRun.filter(status=TaskStatus.RUNNING)
            .filter(Q(last_heartbeat__lt=cutoff) | Q(last_heartbeat__isnull=True, start_time__lt=cutoff))
            .limit(100)
        )
        if not interrupted_executions:
            return []
        active_worker_ids = await self._get_active_worker_ids()
        if active_worker_ids is None:
            # P1-FN-13: Lease store 不可达 → 保守跳过本轮判死(非致命,下轮再判)
            logger.warning(
                "Lease store 不可达,本轮 get_interrupted_tasks 保守跳过 (candidate={} 条待判)",
                len(interrupted_executions),
            )
            return []
        worker_pub_map = await self._load_worker_public_ids(interrupted_executions, Worker)
        task_map = await self._load_tasks_by_id(interrupted_executions, Task)
        await self._cleanup_orphan_runs(interrupted_executions, task_map, TaskRun=TaskRun, TaskStatus=TaskStatus)
        return self._build_recovery_checkpoints(
            interrupted_executions,
            task_map,
            worker_pub_map=worker_pub_map,
            active_worker_ids=active_worker_ids,
        )

    @staticmethod
    async def _load_worker_public_ids(interrupted_executions, Worker) -> dict[int, str]:
        worker_ids_in_batch = [e.worker_id for e in interrupted_executions if e.worker_id]
        workers = await Worker.filter(id__in=worker_ids_in_batch) if worker_ids_in_batch else []
        return {w.id: w.public_id for w in workers}

    @staticmethod
    async def _load_tasks_by_id(interrupted_executions, Task) -> dict:
        task_ids = [e.task_id for e in interrupted_executions]
        tasks = await Task.filter(id__in=task_ids)
        return {t.id: t for t in tasks}

    @staticmethod
    async def _cleanup_orphan_runs(interrupted_executions, task_map, *, TaskRun, TaskStatus) -> None:
        orphan_executions = [e for e in interrupted_executions if e.task_id not in task_map]
        if not orphan_executions:
            return
        orphan_ids = [e.run_id for e in orphan_executions]
        await TaskRun.filter(run_id__in=orphan_ids).update(
            status=TaskStatus.FAILED,
            error_message="任务已被删除",
            end_time=datetime.now(),
        )
        logger.info(f"已清理 {len(orphan_executions)} 条孤立的执行记录（任务已删除）")

    @classmethod
    def _build_recovery_checkpoints(cls, interrupted_executions, task_map, *, worker_pub_map, active_worker_ids):
        checkpoints = []
        for execution in interrupted_executions:
            task = task_map.get(execution.task_id)
            if not task:
                continue
            # B6: worker 仍持有 lease → 视为存活,跳过恢复(避免双跑)
            pub_id = worker_pub_map.get(execution.worker_id) if execution.worker_id else None
            if pub_id and pub_id in active_worker_ids:
                logger.debug(f"跳过恢复（worker lease 仍活跃）: run_id={execution.run_id} worker={pub_id}")
                continue
            checkpoints.append(cls._checkpoint_for_execution(execution, task))
        return checkpoints

    @staticmethod
    def _checkpoint_for_execution(execution, task):
        checkpoint = None
        if execution.result_data and execution.result_data.get("checkpoint"):
            try:
                checkpoint = TaskCheckpoint.from_dict(execution.result_data["checkpoint"])
                checkpoint.state = CheckpointState.CHECKPOINTED
            except Exception:
                pass
        if not checkpoint:
            checkpoint = TaskCheckpoint(
                run_id=execution.run_id,
                task_id=execution.task_id,
                task_public_id=task.public_id,
                state=CheckpointState.CHECKPOINTED,
                progress=0.0,
                started_at=execution.start_time,
            )
        return checkpoint

    async def _get_active_worker_ids(self) -> set[str] | None:
        """B6/P1-FN-13: 返回 set()=成功；None=Redis 不可达（保守：上层跳过判死轮次，不把空集当"没有活跃 worker"从而误判死所有 RUNNING run）。"""
        try:
            from antcode_core.application.services.lease_service import (
                LeasePolicy,
                LeaseStore,
            )
            from antcode_core.infrastructure.redis import get_redis_client, redis_namespace

            redis = await get_redis_client()
            if redis is None:
                logger.warning("Lease store 不可达(Redis client None),跳过本轮判死")
                return None
            store = LeaseStore(redis, namespace=redis_namespace(), policy=LeasePolicy())
            return set(await store.list_active())
        except Exception as exc:
            logger.warning("读取 active leases 失败(保守跳过判死): {}", exc)
            return None

    async def update_progress(self, run_id, progress, checkpoint_data=None):
        """更新任务进度"""
        try:
            checkpoint = await self.get_checkpoint(run_id)
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
                            f"任务 {checkpoint.run_id} 重试次数过多 ({checkpoint.retry_count}次)，标记为失败"
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
                    logger.error(f"恢复任务 {checkpoint.run_id} 异常: {e}")
                    stats["failed"] += 1

            logger.info(f"任务恢复完成: 成功 {stats['recovered']}, 失败 {stats['failed']}, 跳过 {stats['skipped']}")

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

            await TaskRun.filter(run_id=checkpoint.run_id).update(
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
                    "_previous_run_id": checkpoint.run_id,
                }

            if resume_data:
                execution_params = task.execution_params or {}
                execution_params.update(resume_data)
                task.execution_params = execution_params
                await task.save(update_fields=["execution_params"])
                logger.debug(f"已注入断点续传数据到任务 {task.public_id}")

            from antcode_master.control.scheduler_loop import scheduler_service

            await scheduler_service.trigger_task(task.id)

            logger.info(
                f"任务已恢复调度: run_id={checkpoint.run_id}, "
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

            await TaskRun.filter(run_id=checkpoint.run_id).update(
                status=TaskStatus.FAILED,
                error_message=error_message,
                end_time=datetime.now(),
            )

            await self.persistence.delete_checkpoint(checkpoint.run_id)

        except Exception as e:
            logger.error(f"标记任务失败异常: {e}")

    async def recover_single_task(self, run_id):
        """手动恢复单个任务"""
        checkpoint = await self.persistence.get_checkpoint(run_id)
        if not checkpoint:
            logger.warning(f"未找到检查点: {run_id}")
            return False

        return await self._recover_task(checkpoint)


task_persistence_service = TaskPersistenceService()
task_recovery_service = TaskRecoveryService()
