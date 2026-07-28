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
from tortoise.transactions import in_transaction

from antcode_core.application.services.base import QueryHelper
from antcode_core.application.services.logs.task_log_service import task_log_service
from antcode_core.application.services.monitoring import monitoring_service
from antcode_core.application.services.projects.relation_service import relation_service
from antcode_core.application.services.scheduler.outbox_service import (
    scheduler_outbox_service,
)
from antcode_core.application.services.scheduler.spider_dispatcher import spider_task_dispatcher
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
        # 并发控制 - 限制同时执行的任务数量
        self.concurrency_semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_TASKS)
        self._role = settings.SCHEDULER_ROLE.lower()

    def _refresh_role(self) -> str:
        role = settings.SCHEDULER_ROLE.lower()
        if role != self._role:
            self._role = role
        return self._role

    def _scheduler_enabled(self) -> bool:
        return self._refresh_role() == "master"

    def _control_plane(self) -> bool:
        return self._refresh_role() == "control"

    async def _publish_event(self, event: str, task_id: int, connection=None) -> None:
        if not self._control_plane():
            return
        await scheduler_outbox_service.enqueue(
            event_type=event,
            aggregate_type="task",
            aggregate_id=task_id,
            payload={"task_id": str(task_id)},
            connection=connection,
        )

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

    async def create_task(self, task_data, project_type, user_id, internal_project_id=None, specified_worker_id=None):
        """创建调度任务

        修复顺序:
        1. 先构造 Trigger 验证 schedule 配置(schema 里已经试构造过,这里再保底,
           覆盖 dict 直接传入 / schema 被绕过等情况)。
        2. 在同一个 DB 事务里 Task.create + scheduler.add_job,
           add_job 失败则事务回滚,避免"数据库里躺着一个永远调度不起来的任务"。
        """
        # 使用传入的内部 project_id,或从 task_data 中获取
        project_id = internal_project_id if internal_project_id is not None else task_data.project_id

        # 处理 Worker ID
        worker_internal_id = await self._resolve_worker_internal_id(specified_worker_id)

        # 1) 先构造 Trigger 校验触发器字段/语法。_create_trigger 只读
        #    schedule_type / cron_expression / interval_seconds / scheduled_time,
        #    TaskCreateRequest 上都有,可以直接复用它做鸭子类型输入。
        #    如果失败会抛 ValueError,由上层 API 层映射成 400/422,
        #    此时 DB 尚未写入,不会残留脏任务。
        try:
            self._create_trigger(task_data)
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"任务触发器配置非法: {e}") from e

        # 2) 事务内 Task.create + add_job。add_job 失败会向上抛,
        #    tortoise 的 in_transaction 会自动回滚 Task.create。
        async with in_transaction() as conn:
            task = await Task.create(
                **task_data.model_dump(exclude={"project_id", "specified_worker_id"}),
                project_id=project_id,
                task_type=project_type,
                user_id=user_id,
                specified_worker_id=worker_internal_id,
                using_db=conn,
            )

            # 添加到调度器(注意:add_task 在非 master 角色下只发事件,
            # 不会真的构造 trigger,所以上面 stub 那次试构造是唯一的语法兜底)。
            if task.is_active and self._control_plane():
                await self._publish_event("task_changed", task.id, connection=conn)
            elif task.is_active:
                try:
                    await self.add_task(task)
                except Exception as e:  # noqa: BLE001
                    logger.error(f"任务 {task.name} 加入调度器失败,回滚事务: {e}")
                    # 抛出让 in_transaction 回滚 Task.create
                    raise ValueError(f"任务加入调度器失败: {e}") from e

        # P2: 补齐响应组装依赖的外键 public_id/username 快照,避免
        # TaskResponseBuilder 里 _resolve_public_id 抛"响应对象缺少 project_public_id"。
        from antcode_core.application.services.users.user_service import (
            user_service,
        )
        from antcode_core.domain.models import Project

        project = await Project.get_or_none(id=project_id)
        task.project_public_id = project.public_id if project else None
        creator = await user_service.get_user_by_id(user_id)
        task.created_by_public_id = creator.public_id if creator else None
        task.created_by_username = creator.username if creator else None

        logger.info(f"任务创建成功: {task.name} (ID: {task.id})")
        return task

    async def get_user_tasks(
        self,
        user_id,
        status=None,
        is_active=None,
        page=1,
        size=20,
        specified_worker_id=None,
        worker_id=None,
        project_id=None,
        schedule_type=None,
        search=None,
    ):
        """获取用户任务列表（优化版本）"""
        from tortoise.expressions import Q

        from antcode_core.domain.models import Project, Worker

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
                query = query.filter(Q(name__icontains=keyword) | Q(description__icontains=keyword))

        if project_id:
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

        # Worker 视角筛选：只看与该 Worker 相关的任务
        if worker_id:
            worker = await Worker.filter(public_id=worker_id).first()
            if not worker:
                raise ValueError("指定 Worker 不存在")

            related_project_ids = await Project.filter(
                Q(worker_id=worker.public_id) | Q(bound_worker_id=worker.id) | Q(runtime_worker_id=worker.id)
            ).values_list("id", flat=True)

            worker_query = Q(specified_worker_id=worker.id)
            related_ids = list(related_project_ids)
            if related_ids:
                worker_query = worker_query | Q(project_id__in=related_ids)

            query = query.filter(worker_query)

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

    async def get_task_by_id(self, task_id, user_id):
        """根据ID获取任务（支持 public_id 和内部 id）"""
        from antcode_core.domain.models import Project

        # 使用 QueryHelper 获取任务（自动处理 ID/public_id 和权限检查）
        task = await QueryHelper.get_by_id_or_public_id(Task, task_id, user_id=user_id, check_admin=True)

        if not task:
            return None

        # 获取创建者信息
        users_map = await QueryHelper.batch_get_user_info([task.user_id] if task.user_id else [])
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
                task.project_bound_worker_public_id = bound_worker.public_id if bound_worker else None
            else:
                task.project_bound_worker_name = None
                task.project_bound_worker_public_id = None

        # 填充任务指定 Worker 名称
        if task.specified_worker_id:
            from antcode_core.domain.models import Worker

            specified_worker = await Worker.get_or_none(id=task.specified_worker_id)
            task.specified_worker_name = specified_worker.name if specified_worker else None
            task.specified_worker_public_id = specified_worker.public_id if specified_worker else None
        else:
            task.specified_worker_name = None
            task.specified_worker_public_id = None

        return task

    async def update_task(self, task_id, task_data, user_id):
        """锁定最新任务行，只写 PATCH 请求字段并同步调度控制面。"""
        task = await QueryHelper.get_by_id_or_public_id(Task, task_id, user_id=user_id, check_admin=True)
        if not task:
            return None
        update_data = dict(task_data.model_dump(exclude_unset=True))
        if "specified_worker_id" in update_data:
            update_data["specified_worker_id"] = await self._resolve_worker_internal_id(
                update_data["specified_worker_id"]
            )
        trigger_changed = bool({"cron_expression", "interval_seconds", "scheduled_time"} & update_data.keys())
        control_plane = self._control_plane()
        async with in_transaction("default") as conn:
            task = await Task.filter(id=task.id).using_db(conn).select_for_update().first()
            if task is None:
                return None
            for field, value in update_data.items():
                setattr(task, field, value)
            if trigger_changed:
                self._validate_updated_trigger(task)
            if update_data:
                await task.save(using_db=conn, update_fields=list(update_data))
            if control_plane:
                await self._publish_event("task_changed", task.id, connection=conn)
        if not control_plane:
            await self._sync_updated_task(task, update_data, trigger_changed=trigger_changed)
        logger.info(f"任务更新成功: {task.name} (ID: {task.id})")
        return task

    def _validate_updated_trigger(self, task):
        try:
            self._create_trigger(task)
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"任务触发器配置非法: {e}") from e

    async def _sync_updated_task(self, task, update_data, *, trigger_changed):
        if "is_active" in update_data:
            if task.is_active:
                await self.add_task(task)
            else:
                await self.remove_task(task.id)
        elif trigger_changed and task.is_active and self._scheduler_enabled():
            await self.reschedule_task(task)

    @staticmethod
    async def _resolve_worker_internal_id(worker_public_id):
        if not worker_public_id:
            return None
        from antcode_core.domain.models import Worker

        worker = await Worker.filter(public_id=worker_public_id).first()
        if not worker:
            raise ValueError("指定执行 Worker 不存在")
        return worker.id

    async def reschedule_task(self, task):
        """用新的 trigger 更新 APScheduler 内存中已有的 Job。

        如果 Job 不存在(比如任务之前 is_active=False 从未加入调度器),
        则 fallback 到 add_task 走一次 add_job(replace_existing=True)。
        """
        if not self._scheduler_enabled():
            await self._publish_event("task_changed", task.id)
            return
        trigger = self._create_trigger(task)
        try:
            self.scheduler.reschedule_job(str(task.id), trigger=trigger)
            logger.info(f"任务 {task.name} 触发器已重建")
        except JobLookupError:
            logger.info(f"任务 {task.name} 之前未在调度器中,改为 add_job")
            await self.add_task(task)

    async def delete_task(self, task_id, user_id):
        """删除任务（支持 public_id）"""
        # 使用 QueryHelper 获取任务（自动处理 ID/public_id 和权限检查）
        task = await QueryHelper.get_by_id_or_public_id(Task, task_id, user_id=user_id, check_admin=True)

        if not task:
            return False

        from antcode_core.application.services.crawl.spider_storage_cleanup import (
            SPIDER_WRITABLE_TASK_STATUSES,
            iter_cleanup_run_batches,
        )
        from antcode_core.domain.models import Project

        project = await Project.filter(id=task.project_id).only("public_id").first()
        if not project:
            raise RuntimeError("任务关联项目不存在，拒绝删除")
        project_public_id = project.public_id

        async with in_transaction("default") as conn:
            # P1-DB-04: 活动 run 检查必须在事务内、Task 行锁之后执行。
            # `_claim_task_run` 创建新 run 前同样 select_for_update Task 行，
            # 两者串行化后不再有"检查通过 → 新 run 插入 → 被静默删除"窗口。
            locked = await Task.filter(id=task.id).using_db(conn).select_for_update().only("id").first()
            if locked is None:
                return False
            if await TaskRun.filter(task_id=task.id, status__in=SPIDER_WRITABLE_TASK_STATUSES).using_db(conn).exists():
                raise ValueError("任务存在未终态执行，请先取消并等待执行结束")
            from antcode_core.application.services.logs.task_log_run_guard import (
                delete_run_dependency_rows,
                purge_task_logs_for_runs,
            )

            run_rows = await TaskRun.filter(task_id=task.id).using_db(conn).only("run_id").all()
            run_ids = [r.run_id for r in run_rows if r.run_id]
            if run_ids:
                await delete_run_dependency_rows(conn, run_ids)
                for run_batch in iter_cleanup_run_batches(run_ids):
                    await scheduler_outbox_service.enqueue(
                        event_type="spider_storage_cleanup",
                        aggregate_type="task",
                        aggregate_id=task.id,
                        payload={"run_ids": list(run_batch), "project_id": str(project_public_id)},
                        connection=conn,
                    )

            deleted_count = await TaskRun.filter(task_id=task.id).using_db(conn).delete()
            await task.delete(using_db=conn)
            if self._control_plane():
                await self._publish_event("task_changed", task.id, connection=conn)
        # P1-DB-03: TaskRun 删除已提交后，按 run 级 advisory lock 清扫与在途
        # append_entries 竞态窗口内提交的日志残留（语义见 task_log_run_guard）。
        await purge_task_logs_for_runs(run_ids)
        if self._scheduler_enabled():
            await self.remove_task(task.id)
        if deleted_count > 0:
            logger.info(f"已删除任务 {task.id} 的 {deleted_count} 条执行记录")

        logger.info(f"任务删除成功: {task.name} (ID: {task.id})")
        return True

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
        # 使用 QueryHelper 获取任务（自动处理 ID/public_id 和权限检查）
        task = await QueryHelper.get_by_id_or_public_id(Task, task_id, user_id=user_id, check_admin=True)

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

    async def get_execution_by_id(self, run_id):
        """根据ID获取执行记录"""
        return await TaskRun.get_or_none(run_id=run_id)

    async def get_task_stats(self, task_id, user_id):
        """获取任务统计信息（支持 public_id）

        使用数据库聚合查询优化性能。
        """
        import asyncio

        # 使用 QueryHelper 获取任务（自动处理 ID/public_id 和权限检查）
        task = await QueryHelper.get_by_id_or_public_id(Task, task_id, user_id=user_id, check_admin=True)

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
        completed = (
            await base_query.filter(end_time__isnull=False, start_time__isnull=False)
            .only("start_time", "end_time")
            .limit(1000)
        )

        if completed:
            durations = [(e.end_time - e.start_time).total_seconds() for e in completed]
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

    async def verify_admin_permission(self, user_id):
        """验证管理员权限"""
        try:
            return await QueryHelper.is_admin(user_id)
        except Exception as e:
            logger.error(f"验证管理员权限失败: {e}")
            return False

    async def get_user_task_ids(self, user_id):
        """获取用户所有任务ID列表"""
        tasks = await Task.filter(user_id=user_id).all()
        return [task.id for task in tasks]

    async def get_task_executions_by_task_ids(self, task_ids):
        """根据任务ID列表获取所有执行记录"""
        if not task_ids:
            return []
        return await TaskRun.filter(task_id__in=task_ids).all()

    async def pause_task_by_user(self, task_id, user_id):
        """暂停用户任务（支持 public_id）"""
        # 使用 QueryHelper 获取任务（自动处理 ID/public_id 和权限检查）
        task = await QueryHelper.get_by_id_or_public_id(Task, task_id, user_id=user_id, check_admin=True)

        if not task:
            return False

        try:
            await self.pause_task(task.id)  # 使用内部 ID
        except ValueError:
            return False
        return True

    async def resume_task_by_user(self, task_id, user_id):
        """恢复用户任务（支持 public_id）"""
        # 使用 QueryHelper 获取任务（自动处理 ID/public_id 和权限检查）
        task = await QueryHelper.get_by_id_or_public_id(Task, task_id, user_id=user_id, check_admin=True)

        if not task:
            return False

        try:
            await self.resume_task(task.id)  # 使用内部 ID
        except ValueError:
            return False
        return True

    async def trigger_task_by_user(self, task_id, user_id):
        """立即触发用户任务（支持 public_id）"""
        # 使用 QueryHelper 获取任务（自动处理 ID/public_id 和权限检查）
        task = await QueryHelper.get_by_id_or_public_id(Task, task_id, user_id=user_id, check_admin=True)

        if not task:
            return False

        await self.trigger_task(task.id)  # 使用内部 ID
        return True

    async def get_execution_with_permission(self, run_id, user_id):
        """获取执行记录（带权限验证，支持 public_id 和 run_id UUID）"""
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

    async def shutdown(self):
        """关闭调度器"""
        if not self._scheduler_enabled():
            logger.info(f"调度器未启用 (role={self._role})，跳过关闭")
            return
        self.scheduler.shutdown(wait=True)
        logger.info("任务调度器已关闭")

    async def _load_active_tasks(self):
        """加载活跃任务"""
        active_tasks = await Task.filter(is_active=True).all()
        for task in active_tasks:
            await self.add_task(task)
            logger.info(f"加载任务: {task.name}")

    async def add_task(self, task):
        """添加任务到调度器"""
        if not self._scheduler_enabled():
            await self._publish_event("task_changed", task.id)
            return
        # 创建触发器
        trigger = self._create_trigger(task)

        # 添加作业。P2 §4.4: max_instances 接通任务配置（与 master
        # scheduler_loop.add_task 同语义）。
        try:
            max_instances = max(1, int(getattr(task, "max_instances", 1) or 1))
        except (TypeError, ValueError):
            max_instances = 1
        self.scheduler.add_job(
            func=self._execute_task,
            trigger=trigger,
            id=str(task.id),
            name=task.name,
            kwargs={"task_id": task.id},
            replace_existing=True,
            max_instances=max_instances,
        )

        logger.info(f"任务 {task.name} 已添加到调度器 (max_instances={max_instances})")

    async def remove_task(self, task_id):
        """从调度器移除任务"""
        if not self._scheduler_enabled():
            await self._publish_event("task_changed", task_id)
            return
        try:
            self.scheduler.remove_job(str(task_id))
            logger.info(f"任务 {task_id} 已从调度器移除")
        except JobLookupError:
            logger.warning(f"任务 {task_id} 在调度器中不存在，视为已移除")

    async def pause_task(self, task_id):
        """暂停任务"""
        if not self._scheduler_enabled():
            task = await Task.get(id=task_id)
            task.status = TaskStatus.PAUSED
            task.is_active = False
            async with in_transaction("default") as conn:
                await task.save(using_db=conn)
                await self._publish_event("task_changed", task_id, connection=conn)
            logger.info(f"任务 {task_id} 已暂停")
            return
        try:
            self.scheduler.pause_job(str(task_id))
        except JobLookupError:
            logger.warning(f"任务 {task_id} 在调度器中不存在，可能已执行完成或未激活，无法暂停")
            raise ValueError("任务不存在或已执行完成，无法暂停")

        # 更新数据库状态
        task = await Task.get(id=task_id)
        task.status = TaskStatus.PAUSED
        task.is_active = False
        await task.save()

        logger.info(f"任务 {task_id} 已暂停")

    async def resume_task(self, task_id):
        """恢复任务"""
        if not self._scheduler_enabled():
            task = await Task.get(id=task_id)
            task.status = TaskStatus.PENDING
            task.is_active = True
            async with in_transaction("default") as conn:
                await task.save(using_db=conn)
                await self._publish_event("task_changed", task_id, connection=conn)
            logger.info(f"任务 {task_id} 已恢复")
            return
        try:
            self.scheduler.resume_job(str(task_id))
        except JobLookupError:
            logger.warning(f"任务 {task_id} 在调度器中不存在，可能已执行完成或未激活，无法恢复")
            raise ValueError("任务不存在或已执行完成，无法恢复")

        # 更新数据库状态
        task = await Task.get(id=task_id)
        task.status = TaskStatus.PENDING
        task.is_active = True
        await task.save()

        logger.info(f"任务 {task_id} 已恢复")

    async def trigger_task(self, task_id):
        """立即触发任务"""
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

        try:
            # 获取任务及其关联信息
            task_info = await relation_service.get_task_with_project(task_id)
            if not task_info:
                logger.error(f"任务 {task_id} 不存在")
                return

            task = task_info["task"]
            project = task_info["project"]
            project_detail = task_info["project_detail"]

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
                return

            # 记录并发状态
            max_concurrent = settings.MAX_CONCURRENT_TASKS
            running_count = await TaskRun.filter(status=TaskStatus.RUNNING).count()
            logger.info(f"开始执行任务 {task.name} (当前并发: {running_count}/{max_concurrent})")

            # 创建执行记录
            now = datetime.now(UTC)
            execution = await TaskRun.create(
                run_id=run_id,
                task_id=task.id,  # 应用层外键
                status=TaskStatus.PENDING,
                dispatch_status=DispatchStatus.PENDING,
                runtime_status=None,
                start_time=None,
                retry_count=0,
            )

            await execution.save()

            # 推送开始状态到实时日志流
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
            from antcode_core.application.services.scheduler.execution_resolver import execution_resolver
            from antcode_core.common.exceptions import WorkerUnavailableError

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

                target_worker, strategy = await execution_resolver.resolve_execution_worker(task, project)

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
                    result = await self._execute_rule_task(
                        task,
                        project,
                        project_detail,
                        execution,
                        target_worker=target_worker,
                    )
                else:
                    # 文件/代码项目：分发到 Worker 节点执行
                    result = await self._execute_distributed_task(task, project, run_id, execution, target_worker)

            except WorkerUnavailableError as e:
                await self._log_execution(execution, "ERROR", f"Worker 不可用: {e.message}")
                result = {"success": False, "error": e.message}

            # 处理执行结果
            if result:
                status_at = datetime.now(UTC)
                if result.get("success"):
                    # 检查是否为分布式任务（等待节点执行结果）
                    if result.get("distributed") and result.get("pending"):
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
                        if execution:
                            execution.result_data = result
                            await execution.save(update_fields=["result_data"])

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

                        # 推送成功状态到实时日志流
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
                    if execution:
                        execution.result_data = result
                        await execution.save(update_fields=["result_data"])

                    await execution_status_service.update_dispatch_status(
                        run_id=run_id,
                        status=DispatchStatus.FAILED,
                        status_at=status_at,
                        error_message=error_message,
                    )

                    await self._log_execution(execution, "ERROR", f"任务执行失败: {error_message}")

                    # 推送失败状态到实时日志流
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

                await execution_status_service.update_dispatch_status(
                    run_id=run_id,
                    status=DispatchStatus.FAILED,
                    status_at=datetime.now(UTC),
                    error_message=str(e),
                )

            await self._log_execution(execution, "ERROR", f"任务执行异常: {str(e)}")

        finally:
            # 更新任务下次运行时间（避免覆盖最新状态）
            if task:
                next_run_time = self._get_next_run_time(task_id)
                await Task.filter(id=task.id).update(next_run_time=next_run_time)

            # 只有已创建执行记录的任务才需要记录执行后并发状态。
            if execution:
                max_concurrent = settings.MAX_CONCURRENT_TASKS
                running_count = await TaskRun.filter(status=TaskStatus.RUNNING).count()
                logger.info(f"任务执行完成 (当前并发: {running_count}/{max_concurrent})")

    async def _execute_distributed_task(self, task, project, run_id, execution, target_worker=None):
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

            project_type_str = project.type.value if hasattr(project.type, "value") else str(project.type)
            priority = getattr(task, "priority", None)
            environment_vars = dict(task.environment_vars or {})
            environment_vars.pop("ANTCODE_RUNTIME_ENV", None)
            runtime_env_name = None
            if getattr(project, "env_location", None) == "worker" and project.worker_env_name:
                runtime_env_name = project.worker_env_name

            # 解析项目语言（M5+M8）：code / file 项目查各自 info 表的 language，其他默认 python
            language = "python"
            try:
                if project_type_str == "code":
                    from antcode_core.domain.models.project import ProjectCode

                    code_info = await ProjectCode.get_or_none(project_id=project.id)
                    if code_info and code_info.language:
                        language = code_info.language.strip().lower()
                elif project_type_str == "file":
                    from antcode_core.domain.models.project import ProjectFile

                    file_info = await ProjectFile.get_or_none(project_id=project.id)
                    if file_info and getattr(file_info, "language", None):
                        language = file_info.language.strip().lower()
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"读取 project language 失败，默认 python: {exc}")

            params = dict(task.execution_params or {})
            # 塞入 kwargs.language；worker engine 会把 params.kwargs 展平到 TaskPayload.kwargs
            raw_kwargs = params.get("kwargs")
            kwargs_dict = dict(raw_kwargs) if isinstance(raw_kwargs, dict) else {}
            kwargs_dict.setdefault("language", language)
            params["kwargs"] = kwargs_dict

            result = await worker_task_dispatcher.dispatch_task(
                project_id=project.public_id,
                run_id=run_id,
                params=params,
                environment_vars=environment_vars,
                runtime_env_name=runtime_env_name,
                timeout=task.timeout_seconds or settings.TASK_EXECUTION_TIMEOUT,
                worker_id=target_worker.public_id,
                priority=priority,
                project_type=project_type_str,
            )

            if result.success:
                await self._log_execution(
                    execution,
                    "INFO",
                    f"任务已分发到 Worker {target_worker.name}, 远程任务ID: {result.task_id}",
                )

                execution.result_data = {
                    "distributed": True,
                    "worker_id": target_worker.public_id,
                    "worker_name": target_worker.name,
                    "remote_task_id": result.task_id,
                }
                await execution.save(update_fields=["result_data"])

                return {
                    "success": True,
                    "distributed": True,
                    "pending": True,
                    "message": f"任务已分发到 Worker {target_worker.name}",
                    "worker_id": target_worker.public_id,
                    "worker_name": target_worker.name,
                    "remote_task_id": result.task_id,
                }
            else:
                # T7-B3a (P1-1): 派发失败入补派队列（Redis ZSet，重启不丢），
                # master.RedispatchLoop 每 10s tick 拉起来重试；超阈值前
                # execution 停在当前 status（PENDING/DISPATCHING），不直接
                # 置 FAILED 避免"派发失败一次就永久 FAILED"。
                try:
                    from antcode_core.application.services.scheduler.redispatch_service import (
                        redispatch_service,
                    )

                    await redispatch_service.enqueue(
                        run_id=run_id,
                        task_id=task.id,
                        project_id=project.public_id,
                        params=params,
                        environment_vars=environment_vars,
                        runtime_env_name=runtime_env_name,
                        timeout=task.timeout_seconds or settings.TASK_EXECUTION_TIMEOUT,
                        project_type=project_type_str,
                        attempts=0,
                        reason=result.error or "任务分发失败",
                    )
                except Exception as exc:
                    logger.warning(f"入补派队列失败: {exc}")

                return {
                    "success": False,
                    "error": result.error or "任务分发失败",
                    "queued_for_redispatch": True,
                }

        except Exception as e:
            logger.error(f"分布式执行任务失败: {e}")
            return {"success": False, "error": str(e)}

    async def _execute_rule_task(self, task, project, rule_detail, execution, *, target_worker):
        """执行规则任务 - 根据配置选择执行器"""
        try:
            if not rule_detail:
                return {"success": False, "error": "规则项目详情不存在"}

            # 准备参数
            params = task.execution_params or {}
            params["scheduled_task_id"] = task.id
            params["scheduled_task_name"] = task.name

            result = await spider_task_dispatcher.submit_rule_task(
                project=project,
                rule_detail=rule_detail,
                run_id=execution.run_id,
                params=params,
                worker_id=target_worker.public_id,
                timeout=task.timeout_seconds or settings.TASK_EXECUTION_TIMEOUT,
                priority=getattr(task, "priority", None),
            )
            if not result["success"]:
                return {"success": False, "error": result.get("message", "提交失败")}

            await self._log_execution(
                execution,
                "INFO",
                f"任务已提交到节点 {result.get('worker_name', 'unknown')}: {result.get('task_id')}",
            )
            return {
                "success": True,
                "distributed": True,
                "pending": True,
                "message": f"任务已提交到节点 {result.get('worker_name', 'unknown')}",
                "task_id": result.get("task_id"),
                "worker_id": result.get("worker_id"),
                "worker_name": result.get("worker_name"),
            }

        except Exception as e:
            logger.error(f"执行规则任务失败: {e}")
            return {"success": False, "error": str(e)}

    async def _schedule_retry(self, task, execution):
        """调度重试"""
        execution.retry_count += 1
        retry_delay = task.retry_delay or settings.TASK_RETRY_DELAY
        execution.status = TaskStatus.PENDING
        execution.dispatch_status = DispatchStatus.PENDING
        execution.runtime_status = RuntimeStatus.QUEUED
        execution.next_retry_at = datetime.now(UTC) + timedelta(seconds=retry_delay)
        await execution.save(
            update_fields=[
                "retry_count",
                "status",
                "dispatch_status",
                "runtime_status",
                "next_retry_at",
            ]
        )

        await self._log_execution(
            execution,
            "INFO",
            f"任务将在 {retry_delay} 秒后重试 (第{execution.retry_count}次)",
        )

    async def _log_execution(self, execution, level, message):
        """记录执行日志"""
        if not execution:
            return
        log_content = f"[{level}] {message}"
        log_type = "stderr" if level.upper() in ("ERROR", "CRITICAL") else "stdout"
        await task_log_service.write_log(execution.run_id, log_type, log_content)

    async def _push_execution_status(self, execution, status_data):
        """推送执行状态（预留接口）"""
        pass

    def _get_next_run_time(self, task_id):
        """获取下次运行时间"""
        job = self.scheduler.get_job(str(task_id))
        if job and job.next_run_time:
            return job.next_run_time
        return None

    async def get_execution_stats(self):
        """获取任务执行统计信息"""
        total = await TaskRun.all().count()
        running = await TaskRun.filter(status=TaskStatus.RUNNING).count()
        success_count = await TaskRun.filter(status=TaskStatus.SUCCESS).count()
        failed_count = await TaskRun.filter(status=TaskStatus.FAILED).count()
        success_rate = success_count / total * 100 if total else 0
        return {
            "total_executed": total,
            "currently_running": running,
            "failed_count": failed_count,
            "success_count": success_count,
            "success_rate": success_rate,
            "max_concurrent_tasks": settings.MAX_CONCURRENT_TASKS,
            "available_slots": settings.MAX_CONCURRENT_TASKS - running,
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
