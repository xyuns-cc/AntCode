"""任务调度服务 - 所有任务通过 Worker 节点执行

P2-#33：原文件 28+ 处 inline import（``from antcode_core...``）；这些
模块本身不会与 scheduler_loop 形成循环依赖（``Worker / Task / TaskRun /
execution_status_service`` 都安全），统一提到顶部，减少调用路径热点。

P1-#16：``_execute_task_internal`` 原 334 行，吃光阅读预算。拆成 4 个
私有方法 + ``_track_execution`` ``contextmanager`` 把 ``currently_running``
计数收敛，避免提前 ``return`` 时漏减（P2-#35）。
"""

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime, timedelta

from antcode_core.application.services.base import QueryHelper
from antcode_core.application.services.logs.task_log_service import task_log_service
from antcode_core.application.services.monitoring import monitoring_service
from antcode_core.application.services.projects.relation_service import relation_service
from antcode_core.application.services.scheduler.execution_status_service import (
    execution_status_service,
)
from antcode_core.common.config import settings
from antcode_core.domain.models import Worker
from antcode_core.domain.models.enums import (
    DispatchStatus,
    ProjectType,
    ScheduleType,
    TaskStatus,
)
from antcode_core.domain.models.task import Task
from antcode_core.domain.models.task_run import TaskRun
from antcode_core.observability.tracing import new_trace, set_current_trace
from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger
from tortoise.expressions import Q
from tortoise.functions import Avg, Count, Max
from tortoise.transactions import in_transaction

from antcode_master.control.dispatcher_loop import spider_task_dispatcher
from antcode_master.control.result_metadata import merge_result_data
from antcode_master.control.retry_intent_guard import (
    RetryExecutionOptions,
    RetryIntent,
    consume_retry_intent,
    validate_retry_source,
)

# retry intent 数据结构与同事务校验/消费原语迁至 retry_intent_guard
# (P1-FN-05);这里 re-export 维持既有 import 路径(retry_loop / tests)。
from antcode_master.control.trigger_idempotency import schedule_idempotent_trigger
from antcode_master.leader import ensure_leader

RETRY_RUN_NAMESPACE = uuid.UUID("cb99efbb-cc79-4aaf-a3db-6d3beaa98d7f")


class SchedulerService:
    """调度器服务"""

    # K3: leader 变更时 standby watcher 的检查周期（秒）。~30s 兼顾一致性与开销。
    _LEADER_POLL_INTERVAL = 30.0

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
        # K3: standby watcher / scheduler 就绪状态
        self._standby_task: asyncio.Task | None = None
        self._scheduler_ready = False
        # P1-FN-05: leader 激活失败标志（readiness 探针读取）与坏任务清单。
        self._activation_failed = False
        self._task_load_failures: list[int] = []

    @property
    def activation_failed(self) -> bool:
        """leader 激活（含活跃任务加载）失败时为 True，readiness 应转 503。"""
        return self._activation_failed

    @property
    def task_load_failures(self) -> list[int]:
        return list(self._task_load_failures)

    async def start(self):
        """启动 scheduler 的 leader-aware watcher。

        K3: 旧实现是一次性 gate——非 leader 就 return，交给"调用方"重启，
        而无人真的调。首启锁抢不到 → 永久 standby，调度/监控 job / worker
        心跳检测全静默死亡。

        新实现启动一个后台 watcher：leader 时启动 APScheduler + 装载 jobs；
        丢主时优雅停 scheduler；持续轮询等下次夺主。
        """
        if self._standby_task is not None:
            return
        self._standby_task = asyncio.create_task(self._leader_watcher_loop())

    async def _leader_watcher_loop(self) -> None:
        """K3 standby watcher：随 leader 状态自动 up/down APScheduler。"""
        while True:
            try:
                is_leader = await ensure_leader()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning(f"scheduler watcher ensure_leader 失败: {exc}")
                is_leader = False

            if is_leader and not self._scheduler_ready:
                await self._activate_scheduler()
            elif not is_leader and self._scheduler_ready:
                await self._deactivate_scheduler()

            try:
                await asyncio.sleep(self._LEADER_POLL_INTERVAL)
            except asyncio.CancelledError:
                break

    async def _activate_scheduler(self) -> None:
        """成为 leader 时启动 APScheduler + 装载 jobs（幂等）。"""
        try:
            if not self.scheduler.running:
                self.scheduler.start()
            await self._load_active_tasks()
            await self._add_monitoring_jobs()
            await self._add_worker_heartbeat_job()
            self._scheduler_ready = True
            self._activation_failed = False
            logger.info("任务调度器已启动（becoming leader）")
        except Exception:
            # P1-FN-05: 激活失败必须对 readiness 可见（activation_failed 由
            # readiness 探针读取 → 容器转 503），不能只打日志继续报 healthy。
            logger.exception("激活调度器失败，标记未就绪，下一轮重试")
            self._scheduler_ready = False
            self._activation_failed = True

    async def _deactivate_scheduler(self) -> None:
        """丢主时优雅停 APScheduler，避免非 leader 继续触发 job。"""
        try:
            if self.scheduler.running:
                self.scheduler.shutdown(wait=False)
                # 重建一个空 scheduler，方便下次成为 leader 时重新装载
                self.scheduler = AsyncIOScheduler(
                    timezone=settings.SCHEDULER_TIMEZONE,
                    job_defaults={
                        "coalesce": True,
                        "max_instances": 3,
                        "misfire_grace_time": 30,
                    },
                )
            self._scheduler_ready = False
            # 复审 F7-A: 丢主后必须复位激活失败标志 —— standby 不承担
            # leader 职责，不应因上一任期的激活失败持续 readiness 503。
            self._activation_failed = False
            logger.info("已放弃 Leader，调度器进入 standby")
        except Exception:
            logger.exception("停用调度器失败")

    async def create_task(self, task_data, project_type, user_id, internal_project_id=None, specified_worker_id=None):
        """创建调度任务"""
        try:
            # 使用传入的内部 project_id，或从 task_data 中获取
            project_id = internal_project_id if internal_project_id is not None else task_data.project_id

            # 处理 Worker ID
            worker_internal_id = None
            if specified_worker_id:
                worker = await Worker.filter(public_id=specified_worker_id).first()
                if worker:
                    worker_internal_id = worker.id
                else:
                    raise ValueError("指定执行 Worker 不存在")

            # 创建任务
            task = await Task.create(
                **task_data.model_dump(exclude={"project_id"}),
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

        except Exception:
            logger.exception("创建任务失败")
            raise

    async def get_user_tasks(self, user_id, status=None, is_active=None, page=1, size=20, specified_worker_id=None):
        """获取用户任务列表（优化版本）"""
        try:
            # 如果user_id为None，表示管理员查看所有任务
            query = Task.all() if user_id is None else Task.filter(user_id=user_id)

            if status is not None:
                query = query.filter(status=status)
            if is_active is not None:
                query = query.filter(is_active=is_active)

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
        except Exception:
            logger.exception("获取用户任务列表失败")
            raise

    async def get_task_by_id(self, task_id, user_id):
        """根据ID获取任务（支持 public_id 和内部 id）"""
        from antcode_core.domain.models import Project

        try:
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
                    bound_worker = await Worker.get_or_none(id=project.bound_worker_id)
                    task.project_bound_worker_name = bound_worker.name if bound_worker else None
                    task.project_bound_worker_public_id = bound_worker.public_id if bound_worker else None
                else:
                    task.project_bound_worker_name = None
                    task.project_bound_worker_public_id = None

            # 填充任务指定 Worker 名称
            if task.specified_worker_id:
                specified_worker = await Worker.get_or_none(id=task.specified_worker_id)
                task.specified_worker_name = specified_worker.name if specified_worker else None
                task.specified_worker_public_id = specified_worker.public_id if specified_worker else None
            else:
                task.specified_worker_name = None
                task.specified_worker_public_id = None

            return task
        except Exception:
            logger.exception("获取任务失败")
            raise

    async def update_task(self, task_id, task_data, user_id):
        """更新任务（支持 public_id）"""
        try:
            # 使用 QueryHelper 获取任务（自动处理 ID/public_id 和权限检查）
            task = await QueryHelper.get_by_id_or_public_id(Task, task_id, user_id=user_id, check_admin=True)

            if not task:
                return None

            # 更新字段
            update_data = task_data.model_dump(exclude_unset=True)
            for field, value in update_data.items():
                setattr(task, field, value)

            await task.save()

            # 如果任务状态改变，更新调度器（使用内部 ID）
            if "is_active" in update_data:
                if task.is_active:
                    await self.add_task(task)
                else:
                    await self.remove_task(task.id)

            logger.info(f"任务更新成功: {task.name} (ID: {task.id})")
            return task

        except Exception:
            logger.exception("更新任务失败")
            raise

    async def delete_task(self, task_id, user_id):
        """删除任务（支持 public_id）"""
        try:
            # 使用 QueryHelper 获取任务（自动处理 ID/public_id 和权限检查）
            task = await QueryHelper.get_by_id_or_public_id(Task, task_id, user_id=user_id, check_admin=True)

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

        except Exception:
            logger.exception("删除任务失败")
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
        except Exception:
            logger.exception("获取任务执行记录失败")
            raise

    async def get_execution_by_id(self, run_id):
        """根据ID获取执行记录"""
        try:
            return await TaskRun.get_or_none(run_id=run_id)
        except Exception:
            logger.exception("获取执行记录失败")
            raise

    async def get_task_stats(self, task_id, user_id):
        """获取任务统计信息（支持 public_id）

        P1-#18：原实现 ``.all()`` 拉表后 in-memory ``sum(1 for ...)``，
        N 大时拖慢响应。改为 Tortoise ``annotate(Count/Avg/Max)`` 单次
        SQL 聚合 + 1 次查询最后一条 ``TaskRun``。
        """
        try:
            # 使用 QueryHelper 获取任务（自动处理 ID/public_id 和权限检查）
            task = await QueryHelper.get_by_id_or_public_id(Task, task_id, user_id=user_id, check_admin=True)

            if not task:
                return None

            # 单次聚合 SQL：count + 各 status 计数 + 平均时长 + 最近 start_time
            agg = (
                await TaskRun.filter(task_id=task.id)
                .annotate(
                    total=Count("id"),
                    success_count=Count("id", _filter=Q(status=TaskStatus.SUCCESS)),
                    failed_count=Count("id", _filter=Q(status=TaskStatus.FAILED)),
                    running_count=Count("id", _filter=Q(status=TaskStatus.RUNNING)),
                    avg_duration=Avg("duration_seconds"),
                    last_start_time=Max("start_time"),
                )
                .first()
                .values(
                    "total",
                    "success_count",
                    "failed_count",
                    "running_count",
                    "avg_duration",
                    "last_start_time",
                )
            )

            total = (agg or {}).get("total") or 0
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

            success_count = agg.get("success_count") or 0
            failed_count = agg.get("failed_count") or 0
            running_count = agg.get("running_count") or 0
            avg_duration = float(agg.get("avg_duration") or 0.0)
            last_start_time = agg.get("last_start_time")

            # 取最近一条 TaskRun 拼 last_execution（单次小查询，索引命中）
            last_execution_payload = None
            if last_start_time:
                last_run = await TaskRun.filter(task_id=task.id, start_time=last_start_time).first()
                if last_run:
                    last_execution_payload = {
                        "run_id": last_run.run_id,
                        "status": last_run.status,
                        "start_time": last_run.start_time,
                        "end_time": last_run.end_time,
                    }

            return {
                "task_id": task_id,
                "total_executions": total,
                "success_count": success_count,
                "failed_count": failed_count,
                "running_count": running_count,
                "success_rate": success_count / total * 100,
                "avg_duration": avg_duration,
                "last_execution": last_execution_payload,
            }
        except Exception:
            logger.exception("获取任务统计失败")
            raise

    async def verify_admin_permission(self, user_id):
        """验证管理员权限"""
        try:
            return await QueryHelper.is_admin(user_id)
        except Exception:
            logger.exception("验证管理员权限失败")
            return False

    async def get_user_task_ids(self, user_id):
        """获取用户所有任务ID列表"""
        try:
            tasks = await Task.filter(user_id=user_id).all()
            return [task.id for task in tasks]
        except Exception:
            logger.exception("获取用户任务ID失败")
            return []

    async def get_task_executions_by_task_ids(self, task_ids):
        """根据任务ID列表获取所有执行记录"""
        try:
            if not task_ids:
                return []
            return await TaskRun.filter(task_id__in=task_ids).all()
        except Exception:
            logger.exception("获取任务执行记录失败")
            return []

    async def pause_task_by_user(self, task_id, user_id):
        """暂停用户任务（支持 public_id）"""
        try:
            # 使用 QueryHelper 获取任务（自动处理 ID/public_id 和权限检查）
            task = await QueryHelper.get_by_id_or_public_id(Task, task_id, user_id=user_id, check_admin=True)

            if not task:
                return False

            try:
                await self.pause_task(task.id)  # 使用内部 ID
            except ValueError:
                return False
            return True
        except Exception:
            logger.exception("暂停任务失败")
            raise

    async def resume_task_by_user(self, task_id, user_id):
        """恢复用户任务（支持 public_id）"""
        try:
            # 使用 QueryHelper 获取任务（自动处理 ID/public_id 和权限检查）
            task = await QueryHelper.get_by_id_or_public_id(Task, task_id, user_id=user_id, check_admin=True)

            if not task:
                return False

            try:
                await self.resume_task(task.id)  # 使用内部 ID
            except ValueError:
                return False
            return True
        except Exception:
            logger.exception("恢复任务失败")
            raise

    async def trigger_task_by_user(self, task_id, user_id):
        """立即触发用户任务（支持 public_id）"""
        try:
            # 使用 QueryHelper 获取任务（自动处理 ID/public_id 和权限检查）
            task = await QueryHelper.get_by_id_or_public_id(Task, task_id, user_id=user_id, check_admin=True)

            if not task:
                return False

            await self.trigger_task(task.id)  # 使用内部 ID
            return True
        except Exception:
            logger.exception("触发任务失败")
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
                    worker = await Worker.get_or_none(id=execution.worker_id)
                    execution.worker_public_id = worker.public_id if worker else None
                return execution
        except Exception:
            logger.exception("获取执行记录失败")
            raise

    async def shutdown(self):
        """关闭调度器 + 停 K3 watcher。"""
        try:
            if self._standby_task is not None:
                self._standby_task.cancel()
                try:
                    await self._standby_task
                except (asyncio.CancelledError, Exception):
                    pass
                self._standby_task = None
            if self.scheduler.running:
                self.scheduler.shutdown(wait=True)
            self._scheduler_ready = False
            logger.info("任务调度器已关闭")
        except Exception:
            logger.exception("关闭调度器失败")

    async def _load_active_tasks(self):
        """加载活跃任务。

        P1-FN-05:
        - 单个坏任务（如非法 cron 配置）不得阻断其余任务加载 —— 逐任务
          隔离失败并记录到 ``_task_load_failures``（配置错误重试无益，
          不因此拖垮整个 Master readiness）；
        - 加载查询本身失败必须向上抛，由 ``_activate_scheduler`` 标记
          activation_failed，readiness 转 503 并在下一轮 leader poll 重试。
        """
        active_tasks = await Task.filter(is_active=True).all()
        failed: list[int] = []
        for task in active_tasks:
            try:
                await self.add_task(task)
                logger.info(f"加载任务: {task.name}")
            except Exception:
                logger.exception(f"加载任务失败（跳过并继续）: task_id={task.id} name={task.name}")
                failed.append(task.id)
        self._task_load_failures = failed
        if failed:
            logger.error(f"以下活跃任务加载失败，需人工修复配置: {failed}")

    async def add_task(self, task):
        """添加任务到调度器"""
        try:
            # 创建触发器
            trigger = self._create_trigger(task)

            # 添加作业。P2 §4.4: max_instances 接通任务配置 —— 此前模型可配
            # 但调度器固定用 job_defaults(3)，配置完全不生效。
            self.scheduler.add_job(
                func=self._execute_task,
                trigger=trigger,
                id=str(task.id),
                name=task.name,
                kwargs={"task_id": task.id},
                replace_existing=True,
                max_instances=self._task_max_instances(task),
            )

            logger.info(f"任务 {task.name} 已添加到调度器 (max_instances={self._task_max_instances(task)})")

        except Exception:
            logger.exception("添加任务失败")
            raise

    @staticmethod
    def _task_max_instances(task) -> int:
        try:
            value = int(getattr(task, "max_instances", 1) or 1)
        except (TypeError, ValueError):
            value = 1
        return max(1, value)

    async def remove_task(self, task_id):
        """从调度器移除任务"""
        try:
            self.scheduler.remove_job(str(task_id))
            logger.info(f"任务 {task_id} 已从调度器移除")
        except JobLookupError:
            logger.warning(f"任务 {task_id} 在调度器中不存在，视为已移除")
        except Exception:
            logger.exception("移除任务失败")
            raise

    async def pause_task(self, task_id):
        """暂停任务"""
        try:
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
        except Exception:
            logger.exception("暂停任务失败")
            raise

    async def resume_task(self, task_id):
        """恢复任务"""
        try:
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
        except Exception:
            logger.exception("恢复任务失败")
            raise

    async def trigger_task(self, task_id, idempotency_key: str | None = None):
        """立即触发任务。P1-DB-01: 带 ``idempotency_key``（= outbox_id）时
        走确定性 run_id/job id 路径（``trigger_idempotency``），重放折叠到
        同一 run；无 key 的进程内直调路径保持旧行为。"""
        try:
            if idempotency_key:
                await schedule_idempotent_trigger(
                    self.scheduler, self._execute_task, task_id, idempotency_key=idempotency_key
                )
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

                # 使用唯一的job_id，包含时间戳避免冲突；作业立即执行。
                scheduler_tz = getattr(self.scheduler, "timezone", None)
                run_date = datetime.now(scheduler_tz) if scheduler_tz else datetime.now(UTC)
                self.scheduler.add_job(
                    func=self._execute_task,
                    trigger=DateTrigger(run_date=run_date),
                    id=f"{task_id}_manual_{datetime.now().timestamp()}",
                    kwargs={"task_id": task_id},
                    replace_existing=True,
                )
        except Exception:
            logger.exception("触发任务失败")
            raise

    async def trigger_retry_intent(self, intent: RetryIntent) -> str:
        """直接创建并提交 retry run，返回确定性 run_id。

        P1-FN-10: task busy(concurrent instance 已达上限)时 raise
        RetryClaimBusyError 而不是通用 RuntimeError,让 retry_loop 能识别
        "合法 busy"并 requeue 不计入 MAX_CLAIM_ATTEMPTS —— 避免配置允许
        重试的任务因为 upstream 一直忙就被 poison 判死丢失 retry intent。
        """
        run_id = str(uuid.uuid5(RETRY_RUN_NAMESPACE, f"{intent.source_run_id}:{intent.retry_count}"))
        existing = await TaskRun.get_or_none(run_id=run_id)
        if existing is not None:
            return run_id
        options = RetryExecutionOptions(
            source_run_id=intent.source_run_id,
            retry_count=intent.retry_count,
            run_id=run_id,
        )
        async with self.concurrency_semaphore:
            created_run_id = await self._execute_task_internal(intent.task_id, options)
        if created_run_id is None:
            # P1-FN-10: _claim_task_run None 视为 busy（非失败），抛 RetryClaimBusyError 让 retry_loop 区分对待。
            from antcode_master.control.retry_loop import RetryClaimBusyError

            raise RetryClaimBusyError(f"retry run 未创建(可能 task busy): source_run_id={intent.source_run_id}")
        return created_run_id

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

    async def _execute_task(self, task_id, fixed_run_id: str | None = None):
        """执行任务的核心方法（带并发控制）"""
        # 使用信号量控制并发数
        async with self.concurrency_semaphore:
            await self._execute_task_internal(task_id, fixed_run_id=fixed_run_id)

    @contextlib.contextmanager
    def _track_execution(self):
        """统计 currently_running 的 contextmanager。

        P2-#35：原 ``_execute_task_internal`` 在不同提前 ``return`` 分支
        手动维护计数，容易漏减；这里包成 ``with`` 块，无论何种退出
        路径都保证 ``-1``。
        """
        self.task_execution_stats["total_executed"] += 1
        self.task_execution_stats["currently_running"] += 1
        try:
            yield
        finally:
            self.task_execution_stats["currently_running"] -= 1

    async def _execute_task_internal(
        self,
        task_id,
        retry_options: RetryExecutionOptions | None = None,
        *,
        fixed_run_id: str | None = None,
    ) -> str | None:
        """执行任务的内部实现（瘦壳）。

        把准备 / 分发 / 处理 / 失败 / 收尾 5 段拆到独立方法，主壳里只
        负责 trace 绑定 + 调度顺序。

        U1: 入口加 leader 闸口 —— 过去 leader 失效后 APScheduler 仍可能
        触发已排好的 job,这里直接放弃,避免两个 Master 同时跑同一个任务。
        """
        if not await ensure_leader():
            logger.warning(f"非 Leader,放弃任务执行: task_id={task_id}(可能 leader 在 in-flight 期间发生切换)")
            return None

        run_id = retry_options.run_id if retry_options is not None else (fixed_run_id or str(uuid.uuid4()))

        # P5.4: 在调度入口种入新的 W3C trace 上下文。
        trace_ids = new_trace()
        set_current_trace(trace_ids.traceparent)
        logger.debug(f"调度入口绑定 trace: task_id={task_id} run_id={run_id} traceparent={trace_ids.traceparent}")

        with self._track_execution():
            return await self._run_one_execution(task_id, run_id, retry_options)

    async def _run_one_execution(
        self,
        task_id,
        run_id,
        retry_options: RetryExecutionOptions | None = None,
    ) -> str | None:
        """单次执行的主调度：准备 → 分发 → 处理结果 / 异常 → 收尾。"""
        task = None
        execution = None
        result = None
        result_success = None
        distributed_pending = False

        try:
            prepared = await self._prepare_execution(task_id, run_id, retry_options)
            if prepared is None:
                return None

            task, project, project_detail, execution, now = prepared

            # 推送开始 + 写日志
            await self._push_execution_status(
                execution,
                {
                    "status": "RUNNING",
                    "message": "任务开始执行",
                    "task_name": task.name,
                    "start_time": now.isoformat(),
                },
            )
            await self._log_execution(execution, "INFO", f"开始执行任务: {task.name}")

            # 分发并拿结果
            result = await self._dispatch_and_run(task, project, project_detail, execution, run_id, now)

            # 处理结果
            if result:
                result_success, distributed_pending = await self._record_dispatch_result(execution, run_id, result)

                if result_success is False and task and execution and task.retry_count > 0:
                    if execution.retry_count < task.retry_count:
                        await self._schedule_retry(task, execution)

        except TimeoutError:
            await self._handle_failure(execution, run_id, "任务执行超时", status=TaskStatus.TIMEOUT)

        except Exception as e:
            logger.exception(f"执行任务失败: task_id={task_id} run_id={run_id}")
            if execution is None:
                raise
            await self._handle_failure(execution, run_id, str(e), status=TaskStatus.FAILED)

        finally:
            await self._finalize_stats(
                task=task,
                task_id=task_id,
                run_id=run_id,
                result_success=result_success,
                distributed_pending=distributed_pending,
            )
        return execution.run_id if execution is not None else None

    async def _prepare_execution(
        self,
        task_id,
        run_id,
        retry_options: RetryExecutionOptions | None = None,
    ):
        """加载 task/project，做幂等校验，新建 TaskRun。

        返回 ``None`` 表示跳过执行（任务不存在 / 未激活 / 重复触发）。
        """
        task_info = await relation_service.get_task_with_project(task_id)
        if not task_info:
            logger.error(f"任务 {task_id} 不存在")
            return None

        task = task_info["task"]
        project = task_info["project"]
        project_detail = task_info["project_detail"]

        claimed = await self._claim_task_run(task.id, run_id, retry_options)
        if claimed is None:
            return None
        task, execution = claimed

        current_running = self.task_execution_stats["currently_running"]
        logger.info(f"开始执行任务 {task.name} (当前并发: {current_running}/{settings.MAX_CONCURRENT_TASKS})")

        now = datetime.now(UTC)
        self.running_tasks[run_id] = {
            "task_id": task_id,
            "task_name": task.name,
            "start_time": now,
        }

        return task, project, project_detail, execution, now

    async def _claim_task_run(
        self,
        task_id: int,
        run_id: str,
        retry_options: RetryExecutionOptions | None = None,
    ):
        """Atomically claim an idle Task and create its TaskRun."""
        busy_statuses = (
            TaskStatus.RUNNING,
            TaskStatus.DISPATCHING,
            TaskStatus.QUEUED,
        )
        async with in_transaction("default") as conn:
            if retry_options is not None:
                await self._validate_retry_source(conn, task_id, retry_options)
            task = await Task.filter(id=task_id).using_db(conn).select_for_update().first()
            if task is None:
                return None
            if not task.is_active:
                logger.warning(f"任务 {task.name} 未激活，跳过执行")
                return None
            if task.status in busy_statuses:
                # P2 §4.4: max_instances 支持 —— busy 状态下按活跃 run 数判定，
                # 允许配置了并发实例的任务在旧 run 未结束时再次执行。
                # max_instances<=1 保持原语义（busy 即跳过），不做额外查询。
                allowed = self._task_max_instances(task)
                if allowed <= 1:
                    logger.warning(f"任务 {task.name} 正在执行中 (状态: {task.status})，跳过重复触发")
                    return None
                active_runs = (
                    await TaskRun.filter(
                        task_id=task.id,
                        status__in=list(busy_statuses) + [TaskStatus.PENDING],
                    )
                    .using_db(conn)
                    .count()
                )
                if active_runs >= allowed:
                    logger.warning(f"任务 {task.name} 活跃实例已达上限 ({active_runs}/{allowed})，跳过重复触发")
                    return None
            updated = (
                await Task.filter(id=task.id)
                .using_db(conn)
                .update(
                    status=TaskStatus.QUEUED,
                )
            )
            if updated != 1:
                return None
            retry_count = retry_options.retry_count if retry_options is not None else 0
            result_data = None
            if retry_options is not None:
                result_data = {"retry_source_run_id": retry_options.source_run_id}
                await self._consume_retry_intent(conn, retry_options)
            execution = await TaskRun.create(
                run_id=run_id,
                task_id=task.id,
                status=TaskStatus.QUEUED,
                dispatch_status=DispatchStatus.PENDING,
                runtime_status=None,
                start_time=None,
                retry_count=retry_count,
                result_data=result_data,
                using_db=conn,
            )
            task.status = TaskStatus.QUEUED
            return task, execution

    @staticmethod
    async def _consume_retry_intent(conn, options: RetryExecutionOptions) -> None:
        """P1-FN-05: 与新 run 创建同事务清除 durable intent(见 retry_intent_guard)。"""
        await consume_retry_intent(conn, options)

    async def _validate_retry_source(self, conn, task_id: int, options: RetryExecutionOptions) -> None:
        await validate_retry_source(conn, task_id, options)

    async def _dispatch_and_run(self, task, project, project_detail, execution, run_id, now):
        """选 worker → 提交 → 拿单次执行 result dict。失败返回 dict。"""
        from antcode_core.common.exceptions import WorkerUnavailableError

        from antcode_master.dispatch.selector import execution_resolver

        try:
            # P1-FN-01: PENDING→DISPATCHING CAS 作为取消对派发的 fence；失败即中止派发以防已取消 run 被投递。
            claimed = await execution_status_service.update_dispatch_status(
                run_id=run_id,
                status=DispatchStatus.DISPATCHING,
                status_at=now,
            )
            if not claimed:
                logger.warning(f"派发前 CAS 失败(run 已被取消/推进)，中止派发: run_id={run_id}")
                await self._log_execution(execution, "WARNING", "派发被中止: 执行已被取消或状态已变更")
                return {"success": False, "aborted": True, "error": "派发前置 CAS 失败: 执行已被取消或状态已变更"}
            await self._log_execution(execution, "INFO", "正在分配执行节点...")

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
                return await self._execute_rule_task(
                    task,
                    project,
                    project_detail,
                    execution,
                    target_worker=target_worker,
                )
            return await self._execute_distributed_task(task, project, run_id, execution, target_worker)

        except WorkerUnavailableError as e:
            await self._log_execution(execution, "ERROR", f"节点不可用: {e.message}")
            return {"success": False, "error": e.message}

    async def _record_dispatch_result(self, execution, run_id, result):
        """把 ``result`` dict 落库 + 推 WS + 更新 status_service。

        返回 ``(result_success, distributed_pending)``。
        """
        status_at = datetime.now(UTC)
        if result.get("aborted"):
            # P1-FN-01: 派发前置 CAS 失败：run 状态归取消/推进方，本路径不覆盖不计失败不重试。
            await self._log_execution(execution, "WARNING", f"派发已中止: {result.get('error')}")
            return None, False
        if not result.get("success"):
            await self._persist_result_fields(execution, result)
            error_message = result.get("error") or "任务执行失败"
            # P1-FN-07: 消费 update_dispatch_status 返回值——CAS 冲突时跳过 WS/log/retry，避免为终态 run 重复创建 retry run。
            cas_ok = await execution_status_service.update_dispatch_status(
                run_id=run_id,
                status=DispatchStatus.FAILED,
                status_at=status_at,
                error_message=error_message,
            )
            if not cas_ok:
                # 并发方已推到终态,本次 FAILED 不写日志/WS/retry
                await self._log_execution(
                    execution,
                    "DEBUG",
                    f"忽略迟到的 FAILED(dispatch CAS 已被并发终态占据): {error_message}",
                )
                return None, False
            await self._log_execution(execution, "ERROR", f"任务执行失败: {error_message}")
            await self._push_execution_status(
                execution,
                {"status": "FAILED", "message": "任务执行失败", "error": error_message},
            )
            return False, False

        # success 分支
        if result.get("distributed") and result.get("pending"):
            if execution:
                execution.result_data = merge_result_data(execution.result_data, result)
                await execution.save(update_fields=["result_data"])
            # P1-17: 分发成功后必须显式把 dispatch_status 推到 DISPATCHED,
            # 不能只写 result_data。否则:
            # - reconcile_loop 只查 RUNNING/DISPATCHED 都捞不到分发出去但
            #   worker 没上报 RUNNING 的僵尸任务;
            # - 上游 status 派生器还停留在 DISPATCHING,导致 TaskStatus 也
            #   停在 DISPATCHING,前端展示与真实分发链路脱节。
            # 走 status_service CAS 保证:并发 worker 迟到的 ACKED 报文能
            # 覆盖到,但不会翻转已经命中终态(FAILED/TIMEOUT)的记录。
            await execution_status_service.update_dispatch_status(
                run_id=run_id,
                status=DispatchStatus.DISPATCHED,
                status_at=status_at,
            )
            await self._log_execution(
                execution,
                "INFO",
                f"任务已分发，等待节点执行: {result.get('message', '')}",
            )
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
            return None, True

        raise RuntimeError(
            f"Master 远程分发成功结果缺少 distributed=true,pending=true: run_id={run_id} result={result!r}"
        )

    @staticmethod
    async def _persist_result_fields(execution, result):
        """把 result 落到 ``execution.result_data``。

        迁移 39 (``remove_task_run_log_file_paths``) 已下线 ``TaskRun``
        的 ``log_file_path`` / ``error_log_path`` 字段，任务日志改由
        ``task_logs`` 表承载，此处不再落磁盘路径。
        """
        if not execution:
            return
        execution.result_data = merge_result_data(execution.result_data, result)
        await execution.save(update_fields=["result_data"])

    async def _handle_failure(self, execution, run_id, error_message, status):
        """异常 / 超时统一收敛：更新 status_service + 写日志。"""
        if execution is None:
            return

        now = datetime.now(UTC)
        if status == TaskStatus.TIMEOUT:
            target_dispatch = DispatchStatus.TIMEOUT
            log_text = "任务执行超时"
        else:
            target_dispatch = DispatchStatus.FAILED
            log_text = f"任务执行异常: {error_message}"

        await execution_status_service.update_dispatch_status(
            run_id=run_id,
            status=target_dispatch,
            status_at=now,
            error_message=error_message,
        )
        await self._log_execution(execution, "ERROR", log_text)

    async def _finalize_stats(self, task, task_id, run_id, result_success, distributed_pending):
        """统计 success/failure，清理 running_tasks，更新 next_run_time。"""
        if result_success is True:
            self.task_execution_stats["success_count"] += 1
        elif result_success is False and not distributed_pending:
            self.task_execution_stats["failed_count"] += 1

        # L1: 无条件驱逐 running_tasks 条目。分布式分发(distributed_pending)
        # 的结果经 ingester 回流,永不经过 SchedulerService 收尾 —— 若这里仍
        # 保留条目,leader 进程生命周期内 running_tasks 会无界增长。该字典仅在
        # 本 scheduler 内部读取(get_running_tasks 无外部调用方),分布式 run 的
        # 生命周期由 ingester 拥有,派发交接完成后即可安全驱逐。
        self.running_tasks.pop(run_id, None)

        if task:
            next_run_time = self._get_next_run_time(task_id)
            await Task.filter(id=task.id).update(next_run_time=next_run_time)

        current_running = self.task_execution_stats["currently_running"]
        logger.info(f"任务执行完成 (当前并发: {current_running}/{settings.MAX_CONCURRENT_TASKS})")

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

            result = await worker_task_dispatcher.dispatch_task(
                project_id=project.public_id,
                run_id=run_id,
                params=task.execution_params,
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

                execution.result_data = merge_result_data(
                    execution.result_data,
                    {
                        "distributed": True,
                        "worker_id": target_worker.public_id,
                        "worker_name": target_worker.name,
                        "remote_task_id": result.task_id,
                    },
                )
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
                return {
                    "success": False,
                    "error": result.error or "任务分发失败",
                }

        except Exception as e:
            logger.exception("分布式执行任务失败")
            return {"success": False, "error": str(e)}

    async def _execute_rule_task(self, task, project, rule_detail, execution, *, target_worker):
        """提交规则任务，分页由 Worker 内的 UniversalRuleSpider 统一执行。"""
        try:
            if not rule_detail:
                return {"success": False, "error": "规则项目详情不存在"}

            params = dict(task.execution_params or {})
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
                error = result.get("error") or result.get("message") or "提交失败"
                return {"success": False, "error": error}

            worker_name = result.get("worker_name", "unknown")
            await self._log_execution(
                execution,
                "INFO",
                f"任务已提交到节点 {worker_name}: {result.get('task_id')}",
            )
            return {
                "success": True,
                "distributed": True,
                "pending": True,
                "message": f"任务已提交到节点 {worker_name}",
                "task_id": result.get("task_id"),
                "worker_id": result.get("worker_id"),
                "worker_name": result.get("worker_name"),
            }

        except Exception as e:
            logger.exception("执行规则任务失败")
            return {"success": False, "error": str(e)}

    async def _schedule_retry(self, task, execution):
        """原子写入绑定原失败 run 的 durable retry intent。"""
        intent = await self._claim_retry_intent(task, execution)
        if intent is None:
            return

        from antcode_master.control.retry_loop import retry_service

        await retry_service.schedule_intent(intent)

        await self._log_execution(
            execution,
            "INFO",
            f"任务将在 {(intent.retry_time - datetime.now(UTC)).total_seconds():.0f} 秒后重试 "
            f"(第{intent.retry_count}次)",
        )

    async def _claim_retry_intent(self, task, execution) -> RetryIntent | None:
        """在 TaskRun 行锁内幂等创建一次 retry intent。"""
        retry_delay = int(task.retry_delay or settings.TASK_RETRY_DELAY)
        async with in_transaction("default") as conn:
            current = await TaskRun.filter(run_id=execution.run_id).using_db(conn).select_for_update().first()
            if current is None:
                raise RuntimeError(f"retry source 不存在: run_id={execution.run_id}")
            if current.next_retry_at is not None:
                return RetryIntent(task.id, current.run_id, current.retry_count, current.next_retry_at)
            if int(current.retry_count or 0) >= int(task.retry_count or 0):
                return None
            retry_count = int(current.retry_count or 0) + 1
            retry_time = datetime.now(UTC) + timedelta(seconds=retry_delay)
            result_data = dict(current.result_data or {})
            result_data["retry_intent"] = {
                "source_run_id": current.run_id,
                "retry_count": retry_count,
                "retry_time": retry_time.isoformat(),
            }
            updated = (
                await TaskRun.filter(id=current.id)
                .using_db(conn)
                .update(
                    retry_count=retry_count,
                    result_data=result_data,
                    next_retry_at=retry_time,
                )
            )
            if updated != 1:
                raise RuntimeError(f"retry intent 持久化失败: run_id={current.run_id}")
        execution.retry_count = retry_count
        execution.result_data = result_data
        execution.next_retry_at = retry_time
        return RetryIntent(task.id, current.run_id, retry_count, retry_time)

    async def _log_execution(self, execution, level, message):
        """记录执行日志到 ``task_logs`` 表（迁移 39 后不再落磁盘）。"""
        if not execution:
            return
        log_type = "stderr" if level.upper() in ("ERROR", "CRITICAL") else "stdout"
        await task_log_service.write_log(execution.run_id, log_type, f"[{level}] {message}")

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
                self.task_execution_stats["success_count"] / max(1, self.task_execution_stats["total_executed"])
            )
            * 100,
            "max_concurrent_tasks": settings.MAX_CONCURRENT_TASKS,
            "available_slots": settings.MAX_CONCURRENT_TASKS - self.task_execution_stats["currently_running"],
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
        except Exception:
            logger.exception("注册监控任务失败")

    async def _process_monitoring_stream(self):
        """处理监控数据流"""
        try:
            processed = await monitoring_service.process_stream()
            if processed:
                logger.debug("处理监控流数据 {} 条", processed)
        except Exception:
            logger.exception("处理监控数据流失败")

    async def _cleanup_monitoring_data(self):
        """清理过期的监控历史数据"""
        try:
            await monitoring_service.cleanup_old_data()
            logger.info("监控历史数据清理完成")
        except Exception:
            logger.exception("清理监控历史数据失败")

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
        except Exception:
            logger.exception("添加节点心跳任务失败")

    async def _check_workers_health(self):
        """执行 Worker 健康检查（智能自适应）"""
        try:
            from antcode_core.application.services.workers.worker_service import worker_service

            await worker_service.smart_health_check()
        except Exception:
            logger.exception("节点健康检查失败")


# 创建全局调度器服务实例
scheduler_service = SchedulerService()
