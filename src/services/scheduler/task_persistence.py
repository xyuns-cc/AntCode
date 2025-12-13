"""任务持久化与恢复服务

提供任务检查点持久化和故障恢复功能:
- TaskCheckpoint: 任务检查点数据结构
- TaskPersistenceService: 检查点持久化服务
- TaskRecoveryService: 任务恢复服务
"""
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, List, Dict, Any

from loguru import logger

from src.infrastructure.cache import unified_cache


class CheckpointState(str, Enum):
    """检查点状态"""
    PENDING = "pending"          # 等待执行
    RUNNING = "running"          # 正在执行
    CHECKPOINTED = "checkpointed"  # 已保存检查点
    COMPLETED = "completed"      # 已完成
    FAILED = "failed"            # 已失败
    RECOVERED = "recovered"      # 已恢复


@dataclass
class TaskCheckpoint:
    """任务检查点"""
    execution_id: str                     # 执行ID
    task_id: int                          # 任务ID
    task_public_id: str                   # 任务公开ID
    node_id: Optional[str] = None         # 执行节点ID
    state: CheckpointState = CheckpointState.PENDING
    progress: float = 0.0                 # 进度 0.0 - 1.0
    checkpoint_data: Dict[str, Any] = field(default_factory=dict)  # 自定义恢复数据
    last_log_offset: int = 0              # 最后日志位置
    started_at: Optional[datetime] = None
    last_checkpoint_at: Optional[datetime] = None
    retry_count: int = 0                  # 恢复重试次数
    error_message: Optional[str] = None   # 错误信息

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        data = asdict(self)
        # 转换 datetime 为字符串
        if self.started_at:
            data['started_at'] = self.started_at.isoformat()
        if self.last_checkpoint_at:
            data['last_checkpoint_at'] = self.last_checkpoint_at.isoformat()
        data['state'] = self.state.value
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TaskCheckpoint':
        """从字典创建"""
        # 转换字符串为 datetime
        if data.get('started_at') and isinstance(data['started_at'], str):
            data['started_at'] = datetime.fromisoformat(data['started_at'])
        if data.get('last_checkpoint_at') and isinstance(data['last_checkpoint_at'], str):
            data['last_checkpoint_at'] = datetime.fromisoformat(data['last_checkpoint_at'])
        # 转换状态
        if data.get('state') and isinstance(data['state'], str):
            data['state'] = CheckpointState(data['state'])
        return cls(**data)


class TaskPersistenceService:
    """任务持久化服务
    
    功能:
    - 保存任务检查点到数据库和缓存
    - 读取检查点用于恢复
    - 查找中断的任务
    - 心跳更新
    """

    CHECKPOINT_CACHE_PREFIX = "checkpoint:"
    CHECKPOINT_CACHE_TTL = 86400  # 24小时
    MAX_RETRY_ON_RECOVERY = 3     # 最大恢复重试次数
    INTERRUPTED_THRESHOLD_MINUTES = 2  # 中断判定阈值 (分钟) - 基于心跳
    HEARTBEAT_INTERVAL_SECONDS = 30    # 心跳间隔 (秒)

    async def save_checkpoint(self, checkpoint: TaskCheckpoint) -> bool:
        """保存任务检查点
        
        同时保存到数据库和缓存，缓存用于快速读取，数据库用于持久化
        """
        try:
            # 更新检查点时间
            checkpoint.last_checkpoint_at = datetime.now()

            # 1. 保存到数据库
            await self._save_to_db(checkpoint)

            # 2. 保存到缓存 (用于快速恢复)
            cache_key = f"{self.CHECKPOINT_CACHE_PREFIX}{checkpoint.execution_id}"
            await unified_cache.set(
                cache_key,
                checkpoint.to_dict(),
                ttl=self.CHECKPOINT_CACHE_TTL
            )

            logger.debug(
                f"检查点已保存: execution_id={checkpoint.execution_id}, "
                f"progress={checkpoint.progress:.1%}"
            )
            return True

        except Exception as e:
            logger.error(f"保存检查点失败: {e}")
            return False

    async def _save_to_db(self, checkpoint: TaskCheckpoint):
        """保存检查点到数据库"""
        from src.models import TaskExecution

        try:
            execution = await TaskExecution.get_or_none(
                execution_id=checkpoint.execution_id
            )
            if execution:
                # 使用 result_data 字段存储检查点
                result_data = execution.result_data or {}
                result_data['checkpoint'] = checkpoint.to_dict()
                execution.result_data = result_data
                await execution.save(update_fields=['result_data'])
        except Exception as e:
            logger.warning(f"保存检查点到数据库失败: {e}")

    async def get_checkpoint(self, execution_id: str) -> Optional[TaskCheckpoint]:
        """获取任务检查点
        
        优先从缓存读取，失败则从数据库读取
        """
        # 1. 尝试从缓存读取
        try:
            cache_key = f"{self.CHECKPOINT_CACHE_PREFIX}{execution_id}"
            data = await unified_cache.get(cache_key)
            if data:
                return TaskCheckpoint.from_dict(data)
        except Exception as e:
            logger.debug(f"从缓存读取检查点失败: {e}")

        # 2. 从数据库读取
        try:
            from src.models import TaskExecution
            execution = await TaskExecution.get_or_none(execution_id=execution_id)
            if execution and execution.result_data:
                checkpoint_data = execution.result_data.get('checkpoint')
                if checkpoint_data:
                    return TaskCheckpoint.from_dict(checkpoint_data)
        except Exception as e:
            logger.warning(f"从数据库读取检查点失败: {e}")

        return None

    async def delete_checkpoint(self, execution_id: str):
        """删除任务检查点"""
        try:
            cache_key = f"{self.CHECKPOINT_CACHE_PREFIX}{execution_id}"
            await unified_cache.delete(cache_key)
        except Exception as e:
            logger.debug(f"删除缓存检查点失败: {e}")

    async def update_heartbeat(self, execution_id: str) -> bool:
        """更新任务心跳
        
        Worker 定期调用此方法上报心跳
        """
        from src.models import TaskExecution

        try:
            updated = await TaskExecution.filter(
                execution_id=execution_id
            ).update(last_heartbeat=datetime.now())
            return updated > 0
        except Exception as e:
            logger.debug(f"更新心跳失败: {e}")
            return False

    async def get_interrupted_tasks(self) -> List[TaskCheckpoint]:
        """获取所有被中断的任务
        
        查找状态为 running 但心跳超时的执行记录
        优先使用 last_heartbeat，没有则使用 start_time
        只返回任务仍然存在的执行记录
        """
        from src.models import TaskExecution, ScheduledTask
        from src.models.enums import TaskStatus
        from tortoise.expressions import Q

        try:
            cutoff = datetime.now() - timedelta(minutes=self.INTERRUPTED_THRESHOLD_MINUTES)

            # 查询正在执行但心跳超时的任务
            # 条件: (有心跳且超时) OR (无心跳且启动时间超时)
            interrupted_executions = await TaskExecution.filter(
                status=TaskStatus.RUNNING
            ).filter(
                Q(last_heartbeat__lt=cutoff) | 
                Q(last_heartbeat__isnull=True, start_time__lt=cutoff)
            ).limit(100)

            if not interrupted_executions:
                return []

            # 批量获取关联的任务信息
            task_ids = [e.task_id for e in interrupted_executions]
            tasks = await ScheduledTask.filter(id__in=task_ids)
            task_map = {t.id: t for t in tasks}

            # 找出任务已被删除的执行记录，直接标记为失败
            orphan_executions = [e for e in interrupted_executions if e.task_id not in task_map]
            if orphan_executions:
                orphan_ids = [e.execution_id for e in orphan_executions]
                await TaskExecution.filter(execution_id__in=orphan_ids).update(
                    status=TaskStatus.FAILED,
                    error_message="任务已被删除",
                    end_time=datetime.now()
                )
                logger.info(f"已清理 {len(orphan_executions)} 条孤立的执行记录（任务已删除）")

            checkpoints = []
            for execution in interrupted_executions:
                # 跳过任务已删除的执行记录
                task = task_map.get(execution.task_id)
                if not task:
                    continue

                # 检查是否有检查点数据
                checkpoint = None
                if execution.result_data and execution.result_data.get('checkpoint'):
                    try:
                        checkpoint = TaskCheckpoint.from_dict(
                            execution.result_data['checkpoint']
                        )
                        checkpoint.state = CheckpointState.CHECKPOINTED
                    except Exception:
                        pass

                # 如果没有检查点，创建一个新的
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

    async def update_progress(
        self,
        execution_id: str,
        progress: float,
        checkpoint_data: Dict[str, Any] = None
    ):
        """更新任务进度
        
        快速更新进度，不做完整的检查点保存
        """
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
    """任务恢复服务
    
    功能:
    - Master 启动时自动恢复中断的任务
    - 单个任务恢复
    - 失败任务标记
    """

    def __init__(self):
        self.persistence = TaskPersistenceService()
        self._recovering = False

    async def recover_on_startup(self) -> Dict[str, int]:
        """Master 启动时恢复中断的任务
        
        Returns:
            恢复统计: {"recovered": N, "failed": M, "skipped": K}
        """
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
                    # 检查重试次数
                    if checkpoint.retry_count >= TaskPersistenceService.MAX_RETRY_ON_RECOVERY:
                        logger.warning(
                            f"任务 {checkpoint.execution_id} 重试次数过多 "
                            f"({checkpoint.retry_count}次)，标记为失败"
                        )
                        await self._mark_task_failed(
                            checkpoint,
                            "任务恢复失败，重试次数超限"
                        )
                        stats["failed"] += 1
                        continue

                    # 尝试恢复
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

    async def _recover_task(self, checkpoint: TaskCheckpoint) -> bool:
        """恢复单个任务
        
        将任务重新加入调度队列，并传递检查点数据用于断点续传
        """
        try:
            from src.models import ScheduledTask, TaskExecution
            from src.models.enums import TaskStatus

            # 获取原任务信息
            task = await ScheduledTask.get_or_none(id=checkpoint.task_id)
            if not task:
                logger.warning(f"任务不存在: task_id={checkpoint.task_id}")
                return False

            # 更新检查点状态
            checkpoint.state = CheckpointState.RECOVERED
            checkpoint.retry_count += 1
            await self.persistence.save_checkpoint(checkpoint)

            # 将原执行记录标记为失败
            await TaskExecution.filter(
                execution_id=checkpoint.execution_id
            ).update(
                status=TaskStatus.FAILED,
                error_message="任务中断，已重新调度",
                end_time=datetime.now()
            )

            # 准备断点续传数据
            resume_data = None
            if checkpoint.checkpoint_data or checkpoint.progress > 0:
                resume_data = {
                    "_resume": True,
                    "_checkpoint": checkpoint.checkpoint_data,
                    "_progress": checkpoint.progress,
                    "_last_log_offset": checkpoint.last_log_offset,
                    "_previous_execution_id": checkpoint.execution_id,
                }

            # 如果有断点数据，临时更新任务的执行参数
            if resume_data:
                execution_params = task.execution_params or {}
                execution_params.update(resume_data)
                task.execution_params = execution_params
                await task.save(update_fields=['execution_params'])
                logger.debug(f"已注入断点续传数据到任务 {task.public_id}")

            # 触发任务重新执行
            from src.services.scheduler import scheduler_service
            await scheduler_service.trigger_task(task.public_id)

            logger.info(
                f"任务已恢复调度: execution_id={checkpoint.execution_id}, "
                f"进度={checkpoint.progress:.1%}, 重试次数={checkpoint.retry_count}, "
                f"断点续传={'是' if resume_data else '否'}"
            )

            return True

        except Exception as e:
            logger.error(f"恢复任务失败: {e}")
            return False

    async def _mark_task_failed(
        self,
        checkpoint: TaskCheckpoint,
        error_message: str
    ):
        """标记任务为失败"""
        try:
            from src.models import TaskExecution
            from src.models.enums import TaskStatus

            await TaskExecution.filter(
                execution_id=checkpoint.execution_id
            ).update(
                status=TaskStatus.FAILED,
                error_message=error_message,
                end_time=datetime.now()
            )

            # 删除检查点
            await self.persistence.delete_checkpoint(checkpoint.execution_id)

        except Exception as e:
            logger.error(f"标记任务失败异常: {e}")

    async def recover_single_task(self, execution_id: str) -> bool:
        """手动恢复单个任务"""
        checkpoint = await self.persistence.get_checkpoint(execution_id)
        if not checkpoint:
            logger.warning(f"未找到检查点: {execution_id}")
            return False

        return await self._recover_task(checkpoint)


# 全局服务实例
task_persistence_service = TaskPersistenceService()
task_recovery_service = TaskRecoveryService()
