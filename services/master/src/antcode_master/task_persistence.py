"""任务检查点持久化与中断扫描。"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from antcode_core.infrastructure.cache import unified_cache
from loguru import logger

from antcode_master.run_recovery_cleanup import cleanup_unrecoverable_runs
from antcode_master.task_recovery_leases import load_active_lease_ids


class CheckpointPersistenceError(RuntimeError):
    """检查点持久化失败。

    写库/写缓存任何一环失败都必须抛出：调用方一旦以为"进度已保存"，
    崩溃后就会按不存在的进度做恢复决策。
    """


class CheckpointState(StrEnum):
    """检查点状态"""

    PENDING = "pending"
    RUNNING = "running"
    CHECKPOINTED = "checkpointed"
    # 存库的进度数据无法解析：既不是"有进度"也不是"没进度"，必须单列一档，
    # 否则损坏数据会被当成有效进度，任务实际从 0 重跑却无人知情。
    CORRUPTED = "corrupted"
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
    RECOVERY_PAGE_SIZE = 100

    async def save_checkpoint(self, checkpoint):
        """保存任务检查点；任一存储失败立即抛错，绝不告诉调用方"已保存"。"""
        checkpoint.last_checkpoint_at = datetime.now()
        await self._save_to_db(checkpoint)

        cache_key = f"{self.CHECKPOINT_CACHE_PREFIX}{checkpoint.run_id}"
        await unified_cache.set(cache_key, checkpoint.to_dict(), ttl=self.CHECKPOINT_CACHE_TTL)

        logger.debug(f"检查点已保存: run_id={checkpoint.run_id}, progress={checkpoint.progress:.1%}")

    async def _save_to_db(self, checkpoint):
        """保存检查点到数据库；run 不存在说明进度无处落盘，必须报错"""
        from antcode_core.domain.models import TaskRun

        execution = await TaskRun.get_or_none(run_id=checkpoint.run_id)
        if execution is None:
            raise CheckpointPersistenceError(f"检查点对应的执行记录不存在: run_id={checkpoint.run_id}")
        result_data = dict(execution.result_data or {})
        result_data["checkpoint"] = checkpoint.to_dict()
        execution.result_data = result_data
        await execution.save(update_fields=["result_data"])

    async def get_checkpoint(self, run_id):
        """获取任务检查点；返回 None 只表示确实没有检查点，读库失败会抛出"""
        cache_key = f"{self.CHECKPOINT_CACHE_PREFIX}{run_id}"
        try:
            data = await unified_cache.get(cache_key)
        except Exception as e:
            # 缓存只是加速层，数据库才是权威来源；这里显式告警后继续读库。
            logger.warning(f"从缓存读取检查点失败，回落数据库: run_id={run_id}, error={e}")
        else:
            if data:
                return TaskCheckpoint.from_dict(data)

        from antcode_core.domain.models import TaskRun

        execution = await TaskRun.get_or_none(run_id=run_id)
        checkpoint_data = (execution.result_data or {}).get("checkpoint") if execution else None
        return TaskCheckpoint.from_dict(checkpoint_data) if checkpoint_data else None

    async def delete_checkpoint(self, run_id):
        """删除任务检查点：缓存与数据库权威副本都要清，失败必须抛出。

        只清缓存是不够的——``_save_to_db`` 把进度写在 ``TaskRun.result_data``
        里，``get_checkpoint`` 缓存未命中时会回落数据库。残留的 DB 副本会让
        已判死的 run 在下一轮恢复里被旧进度"复活"。
        """
        from antcode_core.domain.models import TaskRun

        await unified_cache.delete(f"{self.CHECKPOINT_CACHE_PREFIX}{run_id}")
        execution = await TaskRun.get_or_none(run_id=run_id)
        if execution is None or not (execution.result_data or {}).get("checkpoint"):
            return
        result_data = dict(execution.result_data or {})
        result_data.pop("checkpoint", None)
        execution.result_data = result_data
        await execution.save(update_fields=["result_data"])

    async def get_interrupted_tasks(self):
        """使用稳定 keyset 分页获取全部被中断任务。"""
        from antcode_core.domain.models import Task, TaskRun, Worker
        from antcode_core.domain.models.enums import TaskStatus

        cutoff = datetime.now() - timedelta(minutes=self.INTERRUPTED_THRESHOLD_MINUTES)
        page = await self._load_interrupted_page(TaskRun, TaskStatus, cutoff, after_id=0)
        if not page:
            return []
        active_lease_ids = await load_active_lease_ids()
        if active_lease_ids is None:
            logger.warning("Lease store 不可达,本轮 get_interrupted_tasks 保守跳过")
            return []
        checkpoints = []
        while page:
            checkpoints.extend(
                await self._checkpoints_for_page(
                    page,
                    active_lease_ids,
                    Task=Task,
                    TaskRun=TaskRun,
                    Worker=Worker,
                    TaskStatus=TaskStatus,
                )
            )
            if len(page) < self.RECOVERY_PAGE_SIZE:
                break
            page = await self._load_interrupted_page(TaskRun, TaskStatus, cutoff, after_id=page[-1].id)
        return checkpoints

    async def _checkpoints_for_page(self, page, active_lease_ids, *, Task, TaskRun, Worker, TaskStatus):
        worker_pub_map = await self._load_worker_public_ids(page, Worker)
        task_map = await self._load_tasks_by_id(page, Task)
        await cleanup_unrecoverable_runs(page, task_map, TaskRun=TaskRun, TaskStatus=TaskStatus)
        return self._build_recovery_checkpoints(
            page,
            task_map,
            worker_pub_map=worker_pub_map,
            active_lease_ids=active_lease_ids,
        )

    @classmethod
    async def _load_interrupted_page(cls, TaskRun, TaskStatus, cutoff, *, after_id: int):
        from tortoise.expressions import Q

        stale = Q(last_heartbeat__lt=cutoff) | Q(last_heartbeat__isnull=True, start_time__lt=cutoff)
        return await (
            TaskRun.filter(status=TaskStatus.RUNNING, id__gt=after_id)
            .filter(stale)
            .order_by("id")
            .limit(cls.RECOVERY_PAGE_SIZE)
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

    @classmethod
    def _build_recovery_checkpoints(cls, interrupted_executions, task_map, *, worker_pub_map, active_lease_ids):
        checkpoints = []
        for execution in interrupted_executions:
            task = task_map.get(execution.task_id)
            if not task:
                continue
            pub_id = worker_pub_map.get(execution.worker_id) if execution.worker_id else None
            if cls._run_lease_is_active(execution, pub_id, active_lease_ids):
                logger.debug(f"跳过恢复（run lease 代际仍活跃）: run_id={execution.run_id} worker={pub_id}")
                continue
            checkpoints.append(cls._checkpoint_for_execution(execution, task))
        return checkpoints

    @staticmethod
    def _run_lease_is_active(execution, worker_public_id, active_lease_ids) -> bool:
        current_lease_id = active_lease_ids.get(worker_public_id) if worker_public_id else None
        if current_lease_id is None:
            return False
        if execution.lease_id:
            return execution.lease_id == current_lease_id
        logger.warning(
            "RUNNING run 缺少 lease_id，无法精确判代际，保守跳过恢复: run_id={} worker={}",
            execution.run_id,
            worker_public_id,
        )
        return True

    @classmethod
    def _checkpoint_for_execution(cls, execution, task):
        """把中断的执行转成恢复用检查点，进度是否可信必须如实标记"""
        stored = (execution.result_data or {}).get("checkpoint")
        if not stored:
            # 从未存过进度：状态保持 PENDING，恢复方据此知道这是从头执行
            return cls._progressless_checkpoint(execution, task, state=CheckpointState.PENDING)

        try:
            checkpoint = TaskCheckpoint.from_dict(stored)
        except Exception as e:
            # 损坏的进度不能伪装成有效检查点：标 CORRUPTED 并写明原因，
            # 否则任务实际从 0 重跑（非幂等任务被完整重放）却无任何线索。
            logger.error(f"检查点数据损坏，无有效进度可续: run_id={execution.run_id}, error={e}")
            return cls._progressless_checkpoint(
                execution,
                task,
                state=CheckpointState.CORRUPTED,
                error_message=f"检查点数据损坏，无法续跑: {e}",
            )

        checkpoint.state = CheckpointState.CHECKPOINTED
        return checkpoint

    @staticmethod
    def _progressless_checkpoint(execution, task, *, state, error_message=None):
        """构造一个明确不含进度的检查点"""
        return TaskCheckpoint(
            run_id=execution.run_id,
            task_id=execution.task_id,
            task_public_id=task.public_id,
            state=state,
            progress=0.0,
            started_at=execution.start_time,
            error_message=error_message,
        )


def __getattr__(name: str):
    if name in {"TaskRecoveryService", "task_recovery_service"}:
        from antcode_master import task_recovery

        return getattr(task_recovery, name)
    raise AttributeError(name)
