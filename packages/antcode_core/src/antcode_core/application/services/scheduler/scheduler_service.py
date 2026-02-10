"""任务调度服务 - 所有任务通过 Worker 节点执行"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from antcode_core.common.config import settings
from antcode_core.domain.models.enums import (
    DispatchStatus,
    ProjectType,
    RuntimeStatus,
    ScheduleType,
    TaskStatus,
)
from antcode_core.domain.models.task import Task
from antcode_core.domain.models.task_run import TaskRun
from antcode_core.application.services.base import QueryHelper
from antcode_core.application.services.logs.task_log_service import task_log_service
from antcode_core.application.services.monitoring import monitoring_service
from antcode_core.application.services.projects.relation_service import relation_service
from antcode_core.application.services.scheduler.spider_dispatcher import spider_task_dispatcher


class SchedulerService:
    """调度器服务"""

    def __init__(self):
        self.scheduler = AsyncIOScheduler(
            timezone=settings.SCHEDULER_TIMEZONE,
            job_defaults={
                "coalesce": True,  # 合并错过的执行
                "max_instances": 3,  # 每个任务的最大并发实例数
                "misfire_grace_time": 30,  # 错过执行的宽限时间（秒）
            },
        )
        self.running_tasks = {}

        # 并发控制 - 限制同时执行的任务数量
        self.concurrency_semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_TASKS)
        self.task_execution_stats = {
            "total_executed": 0,
            "currently_running": 0,
            "failed_count": 0,
            "success_count": 0,
        }
        self._role = settings.SCHEDULER_ROLE.lower()
        self._event_stream = settings.scheduler_event_stream
        self._event_client = None

    def _refresh_role(self) -> str:
        role = settings.SCHEDULER_ROLE.lower()
        if role != self._role:
            self._role = role
        self._event_stream = settings.scheduler_event_stream
        return self._role

    def _scheduler_enabled(self) -> bool:
        return self._refresh_role() == "master"

    def _control_plane(self) -> bool:
        return self._refresh_role() == "control"

    async def _publish_event(self, event: str, task_id: int) -> None:
        if not self._control_plane():
            return
        if not settings.REDIS_ENABLED:
            logger.warning("Redis 未启用，无法发布调度事件")
            return
        if self._event_client is None:
            from antcode_core.infrastructure.redis.streams import StreamClient

            self._event_client = StreamClient()
        try:
            await self._event_client.xadd(
                self._event_stream,
                {
                    "event": event,
                    "task_id": str(task_id),
                    "timestamp": datetime.now(UTC).isoformat(),
                },
                maxlen=settings.SCHEDULER_EVENT_MAXLEN,
            )
        except Exception as e:
            logger.warning(f"发布调度事件失败: {e}")

    async def start(self):
        """启动调度器"""
        try:
            if not self._scheduler_enabled():
                logger.info(f"调度器未启用 (role={self._role})，跳过启动")
                return
            self.scheduler.start()
            logger.info("任务调度器已启动")

            # 加载已存在的活跃任务
            await self._load_active_tasks()

            # 注册监控相关的周期任务
            await self._add_monitoring_jobs()

            # 添加节点心跳检测任务
            await self._add_worker_heartbeat_job()

        except Exception as e:
            logger.error(f"启动调度器失败: {e}")
            raise

    async def create_task(
        self, task_data, project_type, user_id, internal_project_id=None, specified_worker_id=None
    ):
        """创建调度任务"""
        try:
            # 使用传入的内部 project_id，或从 task_data 中获取
            project_id = (
                internal_project_id if internal_project_id is not None else task_data.project_id
            )

            # 处理 Worker ID
            from antcode_core.domain.models import Worker

            worker_internal_id = None
            if specified_worker_id:
                worker = await Worker.filter(public_id=specified_worker_id).first()
                if worker:
                    worker_internal_id = worker.id
                else:
                    raise ValueError("指定执行 Worker 不存在")

            # 创建任务
            task = await Task.create(
                **task_data.model_dump(exclude={"project_id", "specified_worker_id"}),
                project_id=project_id,
                task_type=project_type,
                user_id=user_id,
                specified_worker_id=worker_internal_id,
            )

            # 添加到调度器
            if task.is_active:
                await self.add_task(task)

            logger.info(f"任务创建成功: {task.name} (ID: {task.id})")
            return task

        except Exception as e:
            logger.error(f"创建任务失败: {e}")
            raise

    async def get_user_tasks(
        self,
        user_id,
        status=None,
        is_active=None,
        page=1,
        size=20,
        specified_worker_id=None,
        project_id=None,
        schedule_type=None,
        search=None,
    ):
        """获取用户任务列表（优化版本）"""
        try:
            from tortoise.expressions import Q

            from antcode_core.domain.models import Worker

            # 如果user_id为None，表示管理员查看所有任务
            query = Task.all() if user_id is None else Task.filter(user_id=user_id)

            if status is not None:
                query = query.filter(status=status)
            if is_active is not None:
                query = query.filter(is_active=is_active)
            if schedule_type:
                query = query.filter(schedule_type=schedule_type)
            if search:
                keyword = str(search).strip()
                if keyword:
                    query = query.filter(
                        Q(name__icontains=keyword) | Q(description__icontains=keyword)
                    )

            if project_id:
                from antcode_core.domain.models import Project

                project = await QueryHelper.get_by_id_or_public_id(
                    Project,
                    project_id,
                    user_id=user_id,
                    check_admin=True,
                )
                if not project:
                    raise ValueError("项目不存在或无权限访问")
                query = query.filter(project_id=project.id)

            # Worker 筛选
            if specified_worker_id:
                worker = await Worker.filter(public_id=specified_worker_id).first()
                if worker:
                    query = query.filter(specified_worker_id=worker.id)
                else:
                    raise ValueError("指定执行 Worker 不存在")

            total = await query.count()
            offset = (page - 1) * size
            tasks = await query.order_by("-created_at").offset(offset).limit(size)

            # 批量获取创建者用户名和 public_id
            user_ids = list({t.user_id for t in tasks if t.user_id})
            users_map = await QueryHelper.batch_get_user_info(user_ids)

            # 批量获取项目的 public_id
            project_ids = list({t.project_id for t in tasks if t.project_id})
            projects_map = await QueryHelper.batch_get_project_public_ids(project_ids)

            # 批量获取指定 Worker 的 public_id 和名称
            worker_ids = list({t.specified_worker_id for t in tasks if t.specified_worker_id})
            workers_map = await QueryHelper.batch_get_worker_info(worker_ids)

            # 为任务添加创建者、项目和 Worker 信息
            for task in tasks:
                user_info = users_map.get(task.user_id, {})
                task.created_by_username = user_info.get("username")
                task.created_by_public_id = user_info.get("public_id")
                task.project_public_id = projects_map.get(task.project_id)
                worker_info = workers_map.get(task.specified_worker_id, {})
                task.specified_worker_name = worker_info.get("name")
                task.specified_worker_public_id = worker_info.get("public_id")

            return {
                "tasks": tasks,
                "total": total,
                "page": page,
                "size": size,
                "pages": (total + size - 1) // size,
            }
        except Exception as e:
            logger.error(f"获取用户任务列表失败: {e}")
            raise

    async def get_task_by_id(self, task_id, user_id):
        """根据ID获取任务（支持 public_id 和内部 id）"""
        from antcode_core.domain.models import Project

        try:
            # 使用 QueryHelper 获取任务（自动处理 ID/public_id 和权限检查）
            task = await QueryHelper.get_by_id_or_public_id(
                Task, task_id, user_id=user_id, check_admin=True
            )

            if not task:
                return None

            # 获取创建者信息
            users_map = await QueryHelper.batch_get_user_info(
                [task.user_id] if task.user_id else []
            )
            user_info = users_map.get(task.user_id, {})
            task.created_by_username = user_info.get("username")
            task.created_by_public_id = user_info.get("public_id")

            # 获取项目的 public_id 和执行策略配置
            project = await Project.get_or_none(id=task.project_id)
            task.project_public_id = project.public_id if project else None

            # 填充项目执行策略信息
            if project:
                task.project_execution_strategy = project.execution_strategy
                task.project_bound_worker_id = project.bound_worker_id
                # 获取项目绑定 Worker 名称
                if project.bound_worker_id:
                    from antcode_core.domain.models import Worker

                    bound_worker = await Worker.get_or_none(id=project.bound_worker_id)
                    task.project_bound_worker_name = bound_worker.name if bound_worker else None
                    task.project_bound_worker_public_id = (
                        bound_worker.public_id if bound_worker else None
                    )
                else:
                    task.project_bound_worker_name = None
                    task.project_bound_worker_public_id = None

            # 填充任务指定 Worker 名称
            if task.specified_worker_id:
                from antcode_core.domain.models import Worker

                specified_worker = await Worker.get_or_none(id=task.specified_worker_id)
                task.specified_worker_name = specified_worker.name if specified_worker else None
                task.specified_worker_public_id = (
                    specified_worker.public_id if specified_worker else None
                )
            else:
                task.specified_worker_name = None
                task.specified_worker_public_id = None

            return task
        except Exception as e:
            logger.error(f"获取任务失败: {e}")
            raise

    async def update_task(self, task_id, task_data, user_id):
        """更新任务（支持 public_id）"""
        try:
            # 使用 QueryHelper 获取任务（自动处理 ID/public_id 和权限检查）
            task = await QueryHelper.get_by_id_or_public_id(
                Task, task_id, user_id=user_id, check_admin=True
            )

            if not task:
                return None

            # 更新字段
            update_data = task_data.model_dump(exclude_unset=True)
            for field, value in update_data.items():
                setattr(task, field, value)

            await task.save()

            if self._control_plane():
                await self._publish_event("task_changed", task.id)
                logger.info(f"任务更新成功: {task.name} (ID: {task.id})")
                return task

            # 如果任务状态改变，更新调度器（使用内部 ID）
            if "is_active" in update_data:
                if task.is_active:
                    await self.add_task(task)
                else:
                    await self.remove_task(task.id)

            logger.info(f"任务更新成功: {task.name} (ID: {task.id})")
            return task

        except Exception as e:
            logger.error(f"更新任务失败: {e}")
            raise

    async def delete_task(self, task_id, user_id):
        """删除任务（支持 public_id）"""
        try:
            # 使用 QueryHelper 获取任务（自动处理 ID/public_id 和权限检查）
            task = await QueryHelper.get_by_id_or_public_id(
                Task, task_id, user_id=user_id, check_admin=True
            )

            if not task:
                return False

            # 从调度器移除（使用内部 ID）
            await self.remove_task(task.id)

            # 级联删除执行记录
            deleted_count = await TaskRun.filter(task_id=task.id).delete()
            if deleted_count > 0:
                logger.info(f"已删除任务 {task.id} 的 {deleted_count} 条执行记录")

            # 删除数据库记录
            await task.delete()

            logger.info(f"任务删除成功: {task.name} (ID: {task.id})")
            return True

        except Exception as e:
            logger.error(f"删除任务失败: {e}")
            raise

    async def get_task_executions(
        self,
        task_id,
        user_id,
        status=None,
        start_date=None,
        end_date=None,
        page=1,
        size=20,
    ):
        """获取任务执行记录（支持 public_id）"""
        try:
            # 使用 QueryHelper 获取任务（自动处理 ID/public_id 和权限检查）
            task = await QueryHelper.get_by_id_or_public_id(
                Task, task_id, user_id=user_id, check_admin=True
            )

            if not task:
                raise ValueError("任务不存在或无权访问")

            # 使用内部 ID 查询执行记录
            query = TaskRun.filter(task_id=task.id)

            if status is not None:
                query = query.filter(status=status)
            if start_date:
                query = query.filter(start_time__gte=start_date)
            if end_date:
                query = query.filter(start_time__lte=end_date)

            total = await query.count()
            offset = (page - 1) * size
            executions = await query.offset(offset).limit(size).order_by("-start_time")

            # 为每个执行记录添加任务的 public_id
            for execution in executions:
                execution.task_public_id = task.public_id

            worker_ids = list({e.worker_id for e in executions if e.worker_id})
            workers_map = await QueryHelper.batch_get_worker_info(worker_ids)
            for execution in executions:
                worker_info = workers_map.get(execution.worker_id, {})
                execution.worker_public_id = worker_info.get("public_id")

            return {
                "executions": executions,
                "total": total,
                "page": page,
                "size": size,
                "pages": (total + size - 1) // size,
            }
        except Exception as e:
            logger.error(f"获取任务执行记录失败: {e}")
            raise

    async def get_execution_by_id(self, run_id):
        """根据ID获取执行记录"""
        try:
            return await TaskRun.get_or_none(run_id=run_id)
        except Exception as e:
            logger.error(f"获取执行记录失败: {e}")
            raise

    async def get_task_stats(self, task_id, user_id):
        """获取任务统计信息（支持 public_id）

        使用数据库聚合查询优化性能。
        """
        import asyncio

        try:
            # 使用 QueryHelper 获取任务（自动处理 ID/public_id 和权限检查）
            task = await QueryHelper.get_by_id_or_public_id(
                Task, task_id, user_id=user_id, check_admin=True
            )

            if not task:
                return None

            base_query = TaskRun.filter(task_id=task.id)

            # 并行执行统计查询
            total, success_count, failed_count, running_count, last_execution = await asyncio.gather(
                base_query.count(),
                base_query.filter(status=TaskStatus.SUCCESS).count(),
                base_query.filter(status=TaskStatus.FAILED).count(),
                base_query.filter(status=TaskStatus.RUNNING).count(),
                base_query.order_by("-start_time").first(),
            )

            if total == 0:
                return {
                    "task_id": task_id,
                    "total_executions": 0,
                    "success_count": 0,
                    "failed_count": 0,
                    "running_count": 0,
                    "success_rate": 0.0,
                    "avg_duration": 0.0,
                    "last_execution": None,
                }

            # 计算平均执行时长（只查询有完成时间的记录）
            avg_duration = 0.0
            completed = await base_query.filter(
                end_time__isnull=False, start_time__isnull=False
            ).only("start_time", "end_time").limit(1000)

            if completed:
                durations = [
                    (e.end_time - e.start_time).total_seconds() for e in completed
                ]
                avg_duration = sum(durations) / len(durations)

            return {
                "task_id": task_id,
                "total_executions": total,
                "success_count": success_count,
                "failed_count": failed_count,
                "running_count": running_count,
                "success_rate": success_count / total * 100,
                "avg_duration": avg_duration,
                "last_execution": {
                    "run_id": last_execution.run_id,
                    "status": last_execution.status,
                    "start_time": last_execution.start_time,
                    "end_time": last_execution.end_time,
                }
                if last_execution
                else None,
            }
        except Exception as e:
            logger.error(f"获取任务统计失败: {e}")
            raise

    async def verify_admin_permission(self, user_id):
        """验证管理员权限"""
        try:
            return await QueryHelper.is_admin(user_id)
        except Exception as e:
            logger.error(f"验证管理员权限失败: {e}")
            return False

    async def get_user_task_ids(self, user_id):
        """获取用户所有任务ID列表"""
        try:
            tasks = await Task.filter(user_id=user_id).all()
            return [task.id for task in tasks]
        except Exception as e:
            logger.error(f"获取用户任务ID失败: {e}")
            return []

    async def get_task_executions_by_task_ids(self, task_ids):
        """根据任务ID列表获取所有执行记录"""
        try:
            if not task_ids:
                return []
            return await TaskRun.filter(task_id__in=task_ids).all()
        except Exception as e:
            logger.error(f"获取任务执行记录失败: {e}")
            return []

    async def pause_task_by_user(self, task_id, user_id):
        """暂停用户任务（支持 public_id）"""
        try:
            # 使用 QueryHelper 获取任务（自动处理 ID/public_id 和权限检查）
            task = await QueryHelper.get_by_id_or_public_id(
                Task, task_id, user_id=user_id, check_admin=True
            )

            if not task:
                return False

            try:
                await self.pause_task(task.id)  # 使用内部 ID
            except ValueError:
                return False
            return True
        except Exception as e:
            logger.error(f"暂停任务失败: {e}")
            raise

    async def resume_task_by_user(self, task_id, user_id):
        """恢复用户任务（支持 public_id）"""
        try:
            # 使用 QueryHelper 获取任务（自动处理 ID/public_id 和权限检查）
            task = await QueryHelper.get_by_id_or_public_id(
                Task, task_id, user_id=user_id, check_admin=True
            )

            if not task:
                return False

            try:
                await self.resume_task(task.id)  # 使用内部 ID
            except ValueError:
                return False
            return True
        except Exception as e:
            logger.error(f"恢复任务失败: {e}")
            raise

    async def trigger_task_by_user(self, task_id, user_id):
        """立即触发用户任务（支持 public_id）"""
        try:
            # 使用 QueryHelper 获取任务（自动处理 ID/public_id 和权限检查）
            task = await QueryHelper.get_by_id_or_public_id(
                Task, task_id, user_id=user_id, check_admin=True
            )

            if not task:
                return False

            await self.trigger_task(task.id)  # 使用内部 ID
            return True
        except Exception as e:
            logger.error(f"触发任务失败: {e}")
            raise

    async def get_execution_with_permission(self, run_id, user_id):
        """获取执行记录（带权限验证，支持 public_id 和 run_id UUID）"""
        try:
            # 支持多种查询方式
            execution = None

            run_id_str = str(run_id)

            # 先尝试作为 run_id
            execution = await TaskRun.get_or_none(run_id=run_id_str)

            # 如果没找到，尝试作为 public_id
            if not execution and len(run_id_str) <= 32:
                execution = await TaskRun.get_or_none(public_id=run_id_str)

            if not execution:
                return None

            # 检查用户是否为管理员
            is_admin = await QueryHelper.is_admin(user_id)

            if is_admin:
                # 管理员可以查看所有执行记录
                # 添加任务的 public_id
                task = await Task.get_or_none(id=execution.task_id)
                execution.task_public_id = task.public_id if task else None
                if execution.worker_id:
                    from antcode_core.domain.models import Worker

                    worker = await Worker.get_or_none(id=execution.worker_id)
                    execution.worker_public_id = worker.public_id if worker else None
                return execution
            else:
                # 普通用户只能查看自己任务的执行记录
                task = await Task.get_or_none(id=execution.task_id, user_id=user_id)
                if not task:
                    return None

                execution.task_public_id = task.public_id
                if execution.worker_id:
                    from antcode_core.domain.models import Worker

                    worker = await Worker.get_or_none(id=execution.worker_id)
                    execution.worker_public_id = worker.public_id if worker else None
                return execution
        except Exception as e:
            logger.error(f"获取执行记录失败: {e}")
            raise

    async def shutdown(self):
        """关闭调度器"""
        try:
            if not self._scheduler_enabled():
                logger.info(f"调度器未启用 (role={self._role})，跳过关闭")
                return
            self.scheduler.shutdown(wait=True)
            logger.info("任务调度器已关闭")
        except Exception as e:
            logger.error(f"关闭调度器失败: {e}")

    async def _load_active_tasks(self):
        """加载活跃任务"""
        try:
            active_tasks = await Task.filter(is_active=True).all()
            for task in active_tasks:
                await self.add_task(task)
                logger.info(f"加载任务: {task.name}")
        except Exception as e:
            logger.error(f"加载活跃任务失败: {e}")

    async def add_task(self, task):
        """添加任务到调度器"""
        try:
            if not self._scheduler_enabled():
                await self._publish_event("task_changed", task.id)
                return
            # 创建触发器
            trigger = self._create_trigger(task)

            # 添加作业
            self.scheduler.add_job(
                func=self._execute_task,
                trigger=trigger,
                id=str(task.id),
                name=task.name,
                kwargs={"task_id": task.id},
                replace_existing=True,
            )

            logger.info(f"任务 {task.name} 已添加到调度器")

        except Exception as e:
            logger.error(f"添加任务失败: {e}")
            raise

    async def remove_task(self, task_id):
        """从调度器移除任务"""
        try:
            if not self._scheduler_enabled():
                await self._publish_event("task_changed", task_id)
                return
            self.scheduler.remove_job(str(task_id))
            logger.info(f"任务 {task_id} 已从调度器移除")
        except JobLookupError:
            logger.warning(f"任务 {task_id} 在调度器中不存在，视为已移除")
        except Exception as e:
            logger.error(f"移除任务失败: {e}")
            raise

    async def pause_task(self, task_id):
        """暂停任务"""
        try:
            if not self._scheduler_enabled():
                task = await Task.get(id=task_id)
                task.status = TaskStatus.PAUSED
                task.is_active = False
                await task.save()
                await self._publish_event("task_changed", task_id)
                logger.info(f"任务 {task_id} 已暂停")
                return
            self.scheduler.pause_job(str(task_id))

            # 更新数据库状态
            task = await Task.get(id=task_id)
            task.status = TaskStatus.PAUSED
            task.is_active = False
            await task.save()

            logger.info(f"任务 {task_id} 已暂停")
        except JobLookupError:
            logger.warning(f"任务 {task_id} 在调度器中不存在，可能已执行完成或未激活，无法暂停")
            raise ValueError("任务不存在或已执行完成，无法暂停")
        except Exception as e:
            logger.error(f"暂停任务失败: {e}")
            raise

    async def resume_task(self, task_id):
        """恢复任务"""
        try:
            if not self._scheduler_enabled():
                task = await Task.get(id=task_id)
                task.status = TaskStatus.PENDING
                task.is_active = True
                await task.save()
                await self._publish_event("task_changed", task_id)
                logger.info(f"任务 {task_id} 已恢复")
                return
            self.scheduler.resume_job(str(task_id))

            # 更新数据库状态
            task = await Task.get(id=task_id)
            task.status = TaskStatus.PENDING
            task.is_active = True
            await task.save()

            logger.info(f"任务 {task_id} 已恢复")
        except JobLookupError:
            logger.warning(f"任务 {task_id} 在调度器中不存在，可能已执行完成或未激活，无法恢复")
            raise ValueError("任务不存在或已执行完成，无法恢复")
        except Exception as e:
            logger.error(f"恢复任务失败: {e}")
            raise

    async def trigger_task(self, task_id):
        """立即触发任务"""
        try:
            if not self._scheduler_enabled():
                await self._publish_event("task_trigger", task_id)
                logger.info(f"任务 {task_id} 已触发 (事件)")
                return
            # 检查任务是否存在于调度器中
            job = self.scheduler.get_job(str(task_id))
            if job:
                # 如果存在，修改下次运行时间为现在
                try:
                    aware_now = datetime.now(self.scheduler.timezone)
                except Exception:
                    aware_now = datetime.now(UTC)
                job.modify(next_run_time=aware_now)
                logger.info(f"任务 {task_id} 已触发")
            else:
                # 如果不存在，创建一个临时作业来执行
                logger.info(f"任务 {task_id} 不在调度器中，创建临时作业执行")

                # 使用唯一的job_id，包含时间戳避免冲突
                temp_job_id = f"{task_id}_manual_{datetime.now().timestamp()}"

                # 添加一个立即执行的作业
                self.scheduler.add_job(
                    func=self._execute_task,
                    trigger=DateTrigger(
                        run_date=(
                            datetime.now(self.scheduler.timezone)
                            if hasattr(self.scheduler, "timezone") and self.scheduler.timezone
                            else datetime.now(UTC)
                        )
                    ),
                    id=temp_job_id,
                    kwargs={"task_id": task_id},
                    replace_existing=True,
                )
        except Exception as e:
            logger.error(f"触发任务失败: {e}")
            raise

    def _create_trigger(self, task):
        """创建触发器"""
        if task.schedule_type == ScheduleType.CRON:
            return CronTrigger.from_crontab(task.cron_expression)
        elif task.schedule_type == ScheduleType.INTERVAL:
            return IntervalTrigger(seconds=task.interval_seconds)
        elif task.schedule_type == ScheduleType.DATE:
            return DateTrigger(run_date=task.scheduled_time)
        elif task.schedule_type == ScheduleType.ONCE:
            return DateTrigger(run_date=task.scheduled_time or datetime.now())
        else:
            raise ValueError(f"不支持的调度类型: {task.schedule_type}")

    async def _execute_task(self, task_id):
        """执行任务的核心方法（带并发控制）"""
        # 使用信号量控制并发数
        async with self.concurrency_semaphore:
            await self._execute_task_internal(task_id)

    async def _execute_task_internal(self, task_id):
        """执行任务的内部实现"""
        run_id = str(uuid.uuid4())
        task = None
        execution = None
        result = None
        distributed_pending = False
        local_execution = False
        result_success = None

        # 更新统计信息
        self.task_execution_stats["total_executed"] += 1
        self.task_execution_stats["currently_running"] += 1

        try:
            # 获取任务及其关联信息
            task_info = await relation_service.get_task_with_project(task_id)
            if not task_info:
                logger.error(f"任务 {task_id} 不存在")
                return

            task = task_info["task"]
            project = task_info["project"]
            project_detail = task_info["project_detail"]
            local_execution = project.type == ProjectType.RULE

            # 检查任务是否可以执行
            if not task.is_active:
                logger.warning(f"任务 {task.name} 未激活，跳过执行")
                return

            # 防重复执行：检查任务是否正在执行中
            if task.status in (
                TaskStatus.RUNNING,
                TaskStatus.DISPATCHING,
                TaskStatus.QUEUED,
            ):
                logger.warning(f"任务 {task.name} 正在执行中 (状态: {task.status})，跳过重复触发")
                self.task_execution_stats["currently_running"] -= 1
                self.task_execution_stats["total_executed"] -= 1
                return

            # 记录并发状态
            current_running = self.task_execution_stats["currently_running"]
            max_concurrent = settings.MAX_CONCURRENT_TASKS
            logger.info(f"开始执行任务 {task.name} (当前并发: {current_running}/{max_concurrent})")

            # 生成日志文件路径
            log_paths = task_log_service.generate_log_paths(run_id, task.name)

            # 创建执行记录
            now = datetime.now(UTC)
            execution = await TaskRun.create(
                run_id=run_id,
                task_id=task.id,  # 应用层外键
                status=TaskStatus.PENDING,
                dispatch_status=DispatchStatus.PENDING,
                runtime_status=None,
                start_time=None,
                log_file_path=log_paths["log_file_path"],
                error_log_path=log_paths["error_log_path"],
                retry_count=0,
            )

            await execution.save()

            # 记录到运行中任务
            self.running_tasks[run_id] = {
                "task_id": task_id,
                "task_name": task.name,
                "start_time": now,
            }

            # 推送开始状态到WebSocket
            await self._push_execution_status(
                execution,
                {
                    "status": "RUNNING",
                    "message": "任务开始执行",
                    "task_name": task.name,
                    "start_time": now.isoformat(),
                },
            )

            # 记录日志
            await self._log_execution(execution, "INFO", f"开始执行任务: {task.name}")

            # 使用执行策略解析器确定执行节点
            from antcode_core.common.exceptions import WorkerUnavailableError
            from antcode_core.application.services.scheduler.execution_resolver import execution_resolver

            try:
                from antcode_core.application.services.scheduler.execution_status_service import (
                    execution_status_service,
                )

                await execution_status_service.update_dispatch_status(
                    run_id=run_id,
                    status=DispatchStatus.DISPATCHING,
                    status_at=now,
                )

                await self._log_execution(execution, "INFO", "正在分配执行 Worker...")

                target_worker, strategy = await execution_resolver.resolve_execution_worker(
                    task, project
                )

                if local_execution:
                    await execution_status_service.update_runtime_status(
                        run_id=run_id,
                        status=RuntimeStatus.RUNNING,
                        status_at=now,
                    )

                await self._log_execution(
                    execution,
                    "INFO",
                    f"执行策略: {strategy}, 目标 Worker: {target_worker.name}",
                )

                await execution_status_service.update_dispatch_status(
                    run_id=run_id,
                    status=DispatchStatus.DISPATCHING,
                    status_at=datetime.now(UTC),
                    worker_id=target_worker.id,
                )

                if project.type == ProjectType.RULE:
                    # 规则项目：提交到调度网关
                    result = await self._execute_rule_task(task, project, project_detail, execution)
                else:
                    # 文件/代码项目：分发到 Worker 节点执行
                    result = await self._execute_distributed_task(
                        task, project, run_id, execution, target_worker
                    )

            except WorkerUnavailableError as e:
                await self._log_execution(execution, "ERROR", f"Worker 不可用: {e.message}")
                result = {"success": False, "error": e.message}

            # 处理执行结果
            if result:
                status_at = datetime.now(UTC)
                if result.get("success"):
                    # 检查是否为分布式任务（等待节点执行结果）
                    if result.get("distributed") and result.get("pending"):
                        distributed_pending = True
                        if execution:
                            execution.result_data = result
                            await execution.save(update_fields=["result_data"])

                        await self._log_execution(
                            execution,
                            "INFO",
                            f"任务已分发，等待节点执行: {result.get('message', '')}",
                        )

                        # 推送分发成功状态
                        await self._push_execution_status(
                            execution,
                            {
                                "status": "RUNNING",
                                "message": "任务已分发到节点，等待执行结果",
                                "distributed": True,
                                "worker_id": result.get("worker_id"),
                                "worker_name": result.get("worker_name"),
                            },
                        )
                    else:
                        result_success = True
                        if execution:
                            update_fields = ["result_data"]
                            execution.result_data = result
                            if result.get("log_file_path"):
                                execution.log_file_path = result["log_file_path"]
                                update_fields.append("log_file_path")
                            if result.get("error_log_path"):
                                execution.error_log_path = result["error_log_path"]
                                update_fields.append("error_log_path")
                            await execution.save(update_fields=update_fields)

                        await execution_status_service.update_runtime_status(
                            run_id=run_id,
                            status=RuntimeStatus.SUCCESS,
                            status_at=status_at,
                            exit_code=result.get("exit_code"),
                        )

                        await self._log_execution(
                            execution,
                            "INFO",
                            f"任务执行成功: {result.get('message', '执行完成')}",
                        )

                        # 推送成功状态到WebSocket
                        await self._push_execution_status(
                            execution,
                            {
                                "status": "SUCCESS",
                                "message": "任务执行成功",
                                "result": result,
                            },
                        )
                else:
                    error_message = result.get("error") or "任务执行失败"
                    result_success = False
                    if execution:
                        update_fields = ["result_data"]
                        execution.result_data = result
                        if result.get("log_file_path"):
                            execution.log_file_path = result["log_file_path"]
                            update_fields.append("log_file_path")
                        if result.get("error_log_path"):
                            execution.error_log_path = result["error_log_path"]
                            update_fields.append("error_log_path")
                        await execution.save(update_fields=update_fields)

                    if local_execution:
                        await execution_status_service.update_runtime_status(
                            run_id=run_id,
                            status=RuntimeStatus.FAILED,
                            status_at=status_at,
                            error_message=error_message,
                            exit_code=result.get("exit_code"),
                        )
                    else:
                        await execution_status_service.update_dispatch_status(
                            run_id=run_id,
                            status=DispatchStatus.FAILED,
                            status_at=status_at,
                            error_message=error_message,
                        )

                    await self._log_execution(execution, "ERROR", f"任务执行失败: {error_message}")

                    # 推送失败状态到WebSocket
                    await self._push_execution_status(
                        execution,
                        {
                            "status": "FAILED",
                            "message": "任务执行失败",
                            "error": error_message,
                        },
                    )

                    # 检查是否需要重试
                    if task and execution and task.retry_count > 0:
                        if execution.retry_count < task.retry_count:
                            await self._schedule_retry(task, execution)

        except TimeoutError:
            if execution:
                from antcode_core.application.services.scheduler.execution_status_service import (
                    execution_status_service,
                )

                if local_execution:
                    await execution_status_service.update_runtime_status(
                        run_id=run_id,
                        status=RuntimeStatus.TIMEOUT,
                        status_at=datetime.now(UTC),
                        error_message="任务执行超时",
                    )
                else:
                    await execution_status_service.update_dispatch_status(
                        run_id=run_id,
                        status=DispatchStatus.TIMEOUT,
                        status_at=datetime.now(UTC),
                        error_message="任务执行超时",
                    )

            await self._log_execution(execution, "ERROR", "任务执行超时")

        except Exception as e:
            logger.error(f"执行任务失败: {e}")
            if execution:
                from antcode_core.application.services.scheduler.execution_status_service import (
                    execution_status_service,
                )

                if local_execution:
                    await execution_status_service.update_runtime_status(
                        run_id=run_id,
                        status=RuntimeStatus.FAILED,
                        status_at=datetime.now(UTC),
                        error_message=str(e),
                    )
                else:
                    await execution_status_service.update_dispatch_status(
                        run_id=run_id,
                        status=DispatchStatus.FAILED,
                        status_at=datetime.now(UTC),
                        error_message=str(e),
                    )

            await self._log_execution(execution, "ERROR", f"任务执行异常: {str(e)}")

        finally:
            # 更新并发统计
            self.task_execution_stats["currently_running"] -= 1

            # 更新成功/失败统计（分布式任务不在此处统计）
            if result_success is True:
                self.task_execution_stats["success_count"] += 1
            elif result_success is False and not distributed_pending:
                self.task_execution_stats["failed_count"] += 1

            # 清理运行中任务（分布式任务保留，等待节点回调）
            if run_id in self.running_tasks and not distributed_pending:
                del self.running_tasks[run_id]

            # 更新任务下次运行时间（避免覆盖最新状态）
            if task:
                next_run_time = self._get_next_run_time(task_id)
                await Task.filter(id=task.id).update(next_run_time=next_run_time)

            # 记录任务完成状态
            current_running = self.task_execution_stats["currently_running"]
            max_concurrent = settings.MAX_CONCURRENT_TASKS
            logger.info(f"任务执行完成 (当前并发: {current_running}/{max_concurrent})")

    async def _execute_distributed_task(
        self, task, project, run_id, execution, target_worker=None
    ):
        """分发任务到 Worker 执行"""
        from antcode_core.application.services.workers import worker_task_dispatcher

        try:
            if not target_worker:
                return {"success": False, "error": "未指定执行 Worker"}

            await self._log_execution(
                execution,
                "INFO",
                f"分发任务到 Worker: {target_worker.name} ({target_worker.host}:{target_worker.port})",
            )

            project_type_str = (
                project.type.value if hasattr(project.type, "value") else str(project.type)
            )
            priority = getattr(task, "priority", None)
            environment_vars = dict(task.environment_vars or {})
            if getattr(project, "env_location", None) == "worker" and project.worker_env_name:
                environment_vars["ANTCODE_RUNTIME_ENV"] = project.worker_env_name

            result = await worker_task_dispatcher.dispatch_task(
                project_id=project.public_id,
                run_id=run_id,
                params=task.execution_params,
                environment_vars=environment_vars,
                timeout=task.timeout_seconds or settings.TASK_EXECUTION_TIMEOUT,
                worker_id=target_worker.public_id,
                priority=priority,
                project_type=project_type_str,
            )

            if result.get("success"):
                await self._log_execution(
                    execution,
                    "INFO",
                    f"任务已分发到 Worker {target_worker.name}, 远程任务ID: {result.get('task_id')}",
                )

                execution.result_data = {
                    "distributed": True,
                    "worker_id": target_worker.public_id,
                    "worker_name": target_worker.name,
                    "remote_task_id": result.get("task_id"),
                }
                await execution.save(update_fields=["result_data"])

                return {
                    "success": True,
                    "distributed": True,
                    "pending": True,
                    "message": f"任务已分发到 Worker {target_worker.name}",
                    "worker_id": target_worker.public_id,
                    "worker_name": target_worker.name,
                    "remote_task_id": result.get("task_id"),
                }
            else:
                return {
                    "success": False,
                    "error": result.get("error") or "任务分发失败",
                }

        except Exception as e:
            logger.error(f"分布式执行任务失败: {e}")
            return {"success": False, "error": str(e)}

    async def _execute_rule_task(self, task, project, rule_detail, execution):
        """执行规则任务 - 根据配置选择执行器"""
        try:
            if not rule_detail:
                return {"success": False, "error": "规则项目详情不存在"}

            # 准备参数
            params = task.execution_params or {}
            params["scheduled_task_id"] = task.id
            params["scheduled_task_name"] = task.name

            # 根据规则配置决定提交策略
            if (
                rule_detail.pagination_config
                and rule_detail.pagination_config.get("method") == "url_pattern"
            ):
                # URL分页模式：可能需要提交多个任务
                tasks_submitted = []
                start_page = rule_detail.pagination_config.get("start_page", 1)
                max_pages = rule_detail.pagination_config.get("max_pages", 10)

                for page in range(start_page, start_page + max_pages):
                    page_params = params.copy()
                    page_params["page_number"] = page

                    # 替换URL中的页码
                    original_url = rule_detail.target_url
                    if "{}" in original_url:
                        rule_detail.target_url = original_url.format(page)

                    result = await spider_task_dispatcher.submit_rule_task(
                        project=project,
                        rule_detail=rule_detail,
                        run_id=f"{execution.run_id}_page_{page}",
                        params=page_params,
                    )

                    rule_detail.target_url = original_url  # 恢复原URL

                    if result["success"]:
                        tasks_submitted.append(result["task_id"])

                    await self._log_execution(
                        execution,
                        "INFO",
                        f"提交页面 {page} 到节点 {result.get('worker_name', 'unknown')}: {result.get('task_id')}",
                    )

                return {
                    "success": True,
                    "message": f"成功提交 {len(tasks_submitted)} 个任务到执行器",
                    "task_ids": tasks_submitted,
                    "total_pages": len(tasks_submitted),
                }
            else:
                # 单任务提交
                result = await spider_task_dispatcher.submit_rule_task(
                    project=project,
                    rule_detail=rule_detail,
                    run_id=execution.run_id,
                    params=params,
                )

                if result["success"]:
                    await self._log_execution(
                        execution,
                        "INFO",
                        f"任务已提交到节点 {result.get('worker_name', 'unknown')}: {result.get('task_id')}",
                    )

                    return {
                        "success": True,
                        "message": f"任务已提交到节点 {result.get('worker_name', 'unknown')}",
                        "task_id": result.get("task_id"),
                        "worker_id": result.get("worker_id"),
                        "worker_name": result.get("worker_name"),
                    }
                else:
                    return {
                        "success": False,
                        "error": result.get("message", "提交失败"),
                    }

        except Exception as e:
            logger.error(f"执行规则任务失败: {e}")
            return {"success": False, "error": str(e)}

    async def _schedule_retry(self, task, execution):
        """调度重试"""
        execution.retry_count += 1
        await execution.save()

        # 延迟后重试
        retry_delay = task.retry_delay or settings.TASK_RETRY_DELAY

        # 使用唯一的job_id，包含run_id以避免冲突
        job_id = f"{task.id}_retry_{execution.run_id}_{execution.retry_count}"

        # 先尝试移除可能存在的旧作业
        try:
            self.scheduler.remove_job(job_id)
        except Exception:
            pass  # 忽略作业不存在的错误

        try:
            base_time = (
                datetime.now(self.scheduler.timezone)
                if hasattr(self.scheduler, "timezone") and self.scheduler.timezone
                else datetime.now(UTC)
            )
        except Exception:
            base_time = datetime.now(UTC)

        self.scheduler.add_job(
            func=self._execute_task,
            trigger=DateTrigger(run_date=base_time + timedelta(seconds=retry_delay)),
            id=job_id,
            kwargs={"task_id": task.id},
            replace_existing=True,  # 如果存在则替换
        )

        await self._log_execution(
            execution,
            "INFO",
            f"任务将在 {retry_delay} 秒后重试 (第{execution.retry_count}次)",
        )

    async def _log_execution(self, execution, level, message):
        """记录执行日志"""
        if execution and execution.log_file_path:
            # 写入日志文件并实时推送到WebSocket
            log_content = f"[{level}] {message}"
            if level.upper() in ["ERROR", "CRITICAL"]:
                # 错误日志写入错误日志文件
                if execution.error_log_path:
                    await task_log_service.write_log(
                        execution.error_log_path,
                        log_content,
                        run_id=execution.run_id,
                    )
            else:
                # 普通日志写入输出日志文件
                await task_log_service.write_log(
                    execution.log_file_path,
                    log_content,
                    run_id=execution.run_id,
                )

    async def _push_execution_status(self, execution, status_data):
        """推送执行状态（预留接口）"""
        pass

    def _get_next_run_time(self, task_id):
        """获取下次运行时间"""
        job = self.scheduler.get_job(str(task_id))
        if job and job.next_run_time:
            return job.next_run_time
        return None

    def get_running_tasks(self):
        """获取运行中的任务"""
        return list(self.running_tasks.values())

    def get_execution_stats(self):
        """获取任务执行统计信息"""
        return {
            **self.task_execution_stats,
            "success_rate": (
                self.task_execution_stats["success_count"]
                / max(1, self.task_execution_stats["total_executed"])
            )
            * 100,
            "max_concurrent_tasks": settings.MAX_CONCURRENT_TASKS,
            "available_slots": settings.MAX_CONCURRENT_TASKS
            - self.task_execution_stats["currently_running"],
        }

    async def _add_monitoring_jobs(self):
        """注册监控数据处理任务"""
        if not settings.MONITORING_ENABLED:
            logger.info("监控功能未启用，跳过监控任务注册")
            return

        try:
            self.scheduler.add_job(
                func=self._process_monitoring_stream,
                trigger=IntervalTrigger(seconds=settings.MONITOR_STREAM_INTERVAL),
                id="monitoring_process_stream",
                name="监控数据流处理",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=120,
            )

            self.scheduler.add_job(
                func=self._cleanup_monitoring_data,
                trigger=CronTrigger(hour=3, minute=30),
                id="monitoring_cleanup_data",
                name="监控历史数据清理",
                replace_existing=True,
            )
            logger.info("已注册监控数据处理任务")
        except Exception as e:
            logger.error(f"注册监控任务失败: {e}")

    async def _process_monitoring_stream(self):
        """处理监控数据流"""
        try:
            processed = await monitoring_service.process_stream()
            if processed:
                logger.debug("处理监控流数据 {} 条", processed)
        except Exception as e:
            logger.error(f"处理监控数据流失败: {e}")

    async def _cleanup_monitoring_data(self):
        """清理过期的监控历史数据"""
        try:
            await monitoring_service.cleanup_old_data()
            logger.info("监控历史数据清理完成")
        except Exception as e:
            logger.error(f"清理监控历史数据失败: {e}")

    async def _add_worker_heartbeat_job(self):
        """添加节点心跳检测任务（智能自适应）"""
        try:
            from antcode_core.application.services.workers.worker_service import worker_service

            # 初始化节点健康检查器（使用缓存和智能间隔）
            await worker_service.init_heartbeat_cache()

            # 基础心跳间隔3秒，使用智能调度
            self.scheduler.add_job(
                func=self._check_workers_health,
                trigger=IntervalTrigger(seconds=3),
                id="worker_heartbeat_check",
                name="节点心跳检测",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=5,
            )
            logger.info("已添加节点心跳检测任务（智能自适应模式，基础间隔3秒）")
        except Exception as e:
            logger.error(f"添加节点心跳任务失败: {e}")

    async def _check_workers_health(self):
        """执行 Worker 健康检查（智能自适应）"""
        try:
            from antcode_core.application.services.workers.worker_service import worker_service

            await worker_service.smart_health_check()
        except Exception as e:
            logger.error(f"节点健康检查失败: {e}")


# 创建全局调度器服务实例
scheduler_service = SchedulerService()
