"""任务调度控制面服务 —— 任务 CRUD / 读模型 / 调度事件投递。

本服务只在 web_api（控制面）进程内使用：它不持有 APScheduler，也不执行任何
任务。所有会改变调度语义的写操作都在同一事务里写一条 ``scheduler_outbox``
事件，由 ``antcode_master.control.scheduler_loop`` 消费后落到 Master 自己的
APScheduler 上——Master 是唯一的执行权威。
"""

from datetime import datetime

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger
from tortoise.transactions import in_transaction

from antcode_core.application.services.base import QueryHelper
from antcode_core.application.services.run_ownership import resolve_run_owner_id
from antcode_core.application.services.scheduler import task_project_integrity
from antcode_core.application.services.scheduler.outbox_service import (
    scheduler_outbox_service,
)
from antcode_core.application.services.scheduler.schedule_update import (
    TRIGGER_CONFIG_FIELDS,
    normalize_trigger_update,
)
from antcode_core.application.services.scheduler.trigger_identity import dispatch_run_id
from antcode_core.domain.models.enums import (
    ProjectType,
    ScheduleType,
    TaskStatus,
)
from antcode_core.domain.models.task import Task
from antcode_core.domain.models.task_run import TaskRun


class SchedulerService:
    """调度控制面服务"""

    async def _publish_event(self, event: str, task_id: int, connection=None):
        """把调度语义变更写进 outbox 交给 Master。入队失败必须上抛。"""
        return await scheduler_outbox_service.enqueue(
            event_type=event,
            aggregate_type="task",
            aggregate_id=task_id,
            payload={"task_id": str(task_id)},
            connection=connection,
        )

    async def create_task(self, task_data, project_type, user_id, internal_project_id=None, specified_worker_id=None):
        """校验 Trigger，并在事务内创建任务及调度事件。"""
        # 使用传入的内部 project_id,或从 task_data 中获取
        project_id = internal_project_id if internal_project_id is not None else task_data.project_id

        # 处理 Worker ID
        worker_internal_id = await self._resolve_worker_internal_id(specified_worker_id)

        # API schema 可能被内部调用绕过，因此服务层仍需构造 Trigger 校验。
        try:
            self._create_trigger(task_data)
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"任务触发器配置非法: {e}") from e

        # 事务内 Task.create + outbox 入队：任一步失败都由 tortoise 的
        # in_transaction 整体回滚，不会留下"任务已建但 Master 收不到"的空档。
        async with in_transaction() as conn:
            # 与项目级联删除竞争同一条 Project 行锁。入口层的权限读取只能
            # 授权，不能保证父记录在本事务提交时仍存在。
            locked_project = await task_project_integrity.lock_task_project(conn, project_id)
            locked_project_type = ProjectType(locked_project.type)
            if locked_project_type != ProjectType(project_type):
                raise ValueError("项目类型已变化，请刷新后重试")
            task = await Task.create(
                **task_data.model_dump(exclude={"project_id", "specified_worker_id"}),
                project_id=project_id,
                task_type=locked_project_type,
                user_id=user_id,
                specified_worker_id=worker_internal_id,
                using_db=conn,
            )

            # 只有活跃任务才需要通知 Master 上调度器；上面的试构造是 trigger
            # 配置的唯一语法兜底（本进程不再构造真实 Job）。
            if task.is_active:
                await self._publish_event("task_changed", task.id, connection=conn)

        # P2: 补齐响应组装依赖的外键 public_id/username 快照,避免
        # TaskResponseBuilder 里 _resolve_public_id 抛"响应对象缺少 project_public_id"。
        from antcode_core.application.services.users.user_service import user_service
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
                Q(worker_id=worker.public_id) | Q(bound_worker_id=worker.id)
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
        """锁定最新任务行，维护触发器不变量并同步调度控制面。"""
        task = await QueryHelper.get_by_id_or_public_id(Task, task_id, user_id=user_id, check_admin=True)
        if not task:
            return None
        update_data = dict(task_data.model_dump(exclude_unset=True))
        if "specified_worker_id" in update_data:
            update_data["specified_worker_id"] = await self._resolve_worker_internal_id(
                update_data["specified_worker_id"]
            )
        trigger_fields = {"schedule_type", *TRIGGER_CONFIG_FIELDS}
        trigger_update_requested = bool(trigger_fields & update_data.keys())
        async with in_transaction("default") as conn:
            task = await Task.filter(id=task.id).using_db(conn).select_for_update().first()
            if task is None:
                return None
            if trigger_update_requested:
                # trigger_changed 由 Master 侧按最新任务行自行判定，这里只需
                # 保证归一化后的字段集合正确落库。
                update_data, _ = normalize_trigger_update(task, update_data)
            for field, value in update_data.items():
                setattr(task, field, value)
            if trigger_update_requested:
                self._validate_updated_trigger(task)
            if update_data:
                await task.save(using_db=conn, update_fields=list(update_data))
            await self._publish_event("task_changed", task.id, connection=conn)
        logger.info(f"任务更新成功: {task.name} (ID: {task.id})")
        # 事务内锁到的是裸 ORM 行，缺 project_public_id 等投影字段，直接交给
        # TaskResponseBuilder 会抛 ValueError。回读让 PUT 响应与 GET 详情同构。
        return await self.get_task_by_id(task.id, user_id)

    def _validate_updated_trigger(self, task):
        try:
            self._create_trigger(task)
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"任务触发器配置非法: {e}") from e

    @staticmethod
    async def _resolve_worker_internal_id(worker_public_id):
        if not worker_public_id:
            return None
        from antcode_core.domain.models import Worker

        worker = await Worker.filter(public_id=worker_public_id).first()
        if not worker:
            raise ValueError("指定执行 Worker 不存在")
        return worker.id

    async def delete_task(self, task_id, user_id):
        """删除任务（支持 public_id）"""
        # 使用 QueryHelper 获取任务（自动处理 ID/public_id 和权限检查）
        task = await QueryHelper.get_by_id_or_public_id(Task, task_id, user_id=user_id, check_admin=True)

        if not task:
            return False

        from antcode_core.application.services.crawl.spider_storage_cleanup import iter_cleanup_run_batches
        from antcode_core.application.services.workers.run_settlement_guard import (
            load_deletable_run_ids,
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
            from antcode_core.application.services.logs.task_log_run_guard import (
                delete_run_dependency_rows,
                purge_task_logs_for_runs,
            )

            run_ids = await load_deletable_run_ids(conn, [task.id])
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
            await self._publish_event("task_changed", task.id, connection=conn)
        # P1-DB-03: TaskRun 删除已提交后，按 run 级 advisory lock 清扫与在途
        # append_entries 竞态窗口内提交的日志残留（语义见 task_log_run_guard）。
        await purge_task_logs_for_runs(run_ids)
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
        task = await QueryHelper.get_by_id_or_public_id(Task, task_id, user_id=user_id, check_admin=True)

        if not task:
            return False

        await self.pause_task(task.id)  # 使用内部 ID
        return True

    async def resume_task_by_user(self, task_id, user_id):
        """恢复用户任务（支持 public_id）"""
        task = await QueryHelper.get_by_id_or_public_id(Task, task_id, user_id=user_id, check_admin=True)

        if not task:
            return False

        await self.resume_task(task.id)  # 使用内部 ID
        return True

    async def trigger_task_by_user(self, task_id, user_id):
        """立即触发用户任务（支持 public_id）"""
        task = await QueryHelper.get_by_id_or_public_id(Task, task_id, user_id=user_id, check_admin=True)
        if not task:
            return False

        run_id = await self.trigger_task(task.id)  # 使用内部 ID
        return run_id if isinstance(run_id, str) else True

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

        # 普通用户只能看自己的 run。所有者必须按 run 类型解析：计划任务 run 走 Task，
        # 爬取批次 run 没有 Task 行、走 CrawlBatch（见 run_ownership）。只查 Task 会让
        # 用户对自己的批次 run 一律 404。管理员看全部，但仍要解析出 public_id。
        if not await QueryHelper.is_admin(user_id):
            owner_id = await resolve_run_owner_id(execution)
            if owner_id is None or owner_id != user_id:
                return None

        task = await Task.get_or_none(id=execution.task_id)
        execution.task_public_id = task.public_id if task else None
        if execution.worker_id:
            from antcode_core.domain.models import Worker

            worker = await Worker.get_or_none(id=execution.worker_id)
            execution.worker_public_id = worker.public_id if worker else None
        return execution

    async def pause_task(self, task_id):
        """暂停任务：落库为 PAUSED 并通知 Master 摘掉 Job。"""
        task = await Task.get(id=task_id)
        task.status = TaskStatus.PAUSED
        task.is_active = False
        async with in_transaction("default") as conn:
            await task.save(using_db=conn)
            await self._publish_event("task_changed", task_id, connection=conn)
        logger.info(f"任务 {task_id} 已暂停")

    async def resume_task(self, task_id):
        """恢复任务：落库为 PENDING 并通知 Master 重新挂 Job。"""
        task = await Task.get(id=task_id)
        task.status = TaskStatus.PENDING
        task.is_active = True
        async with in_transaction("default") as conn:
            await task.save(using_db=conn)
            await self._publish_event("task_changed", task_id, connection=conn)
        logger.info(f"任务 {task_id} 已恢复")

    async def trigger_task(self, task_id):
        """立即触发任务：run_id 走 ``dispatch_run_id``，与 Master 派发侧同一身份。"""
        task = await Task.get_or_none(id=task_id)
        if task is None:
            raise ValueError(f"触发目标任务不存在: task_id={task_id}")
        event = await self._publish_event("task_trigger", task_id)
        logger.info(f"任务 {task_id} 已触发 (事件)")
        return dispatch_run_id(task, event.public_id)

    def _create_trigger(self, task):
        """构造 Trigger 用于校验调度配置（本进程不会真的挂 Job）。"""
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


# 创建全局调度器服务实例
scheduler_service = SchedulerService()
