"""任务调度服务 - 所有任务通过 Worker 节点执行

P2-#33：原文件 28+ 处 inline import（``from antcode_core...``）；这些
模块本身不会与 scheduler_loop 形成循环依赖（``Task / TaskRun /
execution_status_service`` 都安全），统一提到顶部，减少调用路径热点。

本模块只承担 Master 进程内的调度职责；面向 Web 的读模型（带 ``user_id``
权限校验与分页 DTO 组装）由 core 的 ``scheduler_service`` 提供，此处不再
保留同名副本。

P1-#16：``_execute_task_internal`` 原 334 行，吃光阅读预算。拆成 4 个
私有方法 + ``_track_execution`` ``contextmanager`` 把 ``currently_running``
计数收敛，避免提前 ``return`` 时漏减（P2-#35）。

实时状态帧的边界：``run_status`` 实时帧只有一个发布者——
``ingester/run_status_publisher``，且只由 Worker 结果回传
（``ingester/result_loop``）触发。调度面（本模块 /
``scheduler_failure_wiring`` / ``retry_dispatch_recovery``）**不发布**实时帧，
因此派发开始、派发成功、派发失败、派发超时都不会产生推送帧；前端看到这些
状态靠 web_api SSE 存活连接里 PG 权威的状态兜底
（``streams/log_stream_active._terminal_status_frame``）。这里曾有一个
``_push_execution_status`` 空桩让三处调用点"看起来推送了状态"，已删除，
不要再加回来：现有 ``run_status`` 帧只承载 ``status/progress/message``，
装不下调度面想推的 worker_id / distributed / task_name 等字段。
"""

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime, timedelta

from antcode_core.application.services.lease_fenced_ready_publish import (
    SchedulerDispatchFenceError,
)
from antcode_core.application.services.logs.task_log_service import task_log_service
from antcode_core.application.services.monitoring import monitoring_service
from antcode_core.application.services.projects.relation_service import relation_service
from antcode_core.application.services.scheduler.execution_status_service import (
    execution_status_service,
)
from antcode_core.common.config import settings
from antcode_core.domain.models.enums import (
    DispatchStatus,
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
from tortoise.transactions import in_transaction

from antcode_master.control.dispatcher_loop import spider_task_dispatcher
from antcode_master.control.durable_schedule import (
    ONE_TIME_SCHEDULE_TYPES,
    DurableScheduleRunner,
    create_task_trigger,
)
from antcode_master.control.execution_claim import (
    ExecutionClaimDependencies,
    ExecutionClaimRequest,
    claim_task_run,
)
from antcode_master.control.execution_parameters import (
    RecoveryExecutionOptions,
    effective_execution_params,
)
from antcode_master.control.recovery_execution import (
    lock_interrupted_source,
    transition_interrupted_source,
)
from antcode_master.control.result_data_persistence import merge_dispatch_result_data
from antcode_master.control.retry_decision_history import retry_was_decided
from antcode_master.control.retry_dispatch_recovery import (
    count_active_runs,
    is_recoverable_retry_run,
    resume_retry_run,
    run_prepared_execution,
    task_max_instances,
    validate_existing_retry_run,
)
from antcode_master.control.retry_intent_guard import (
    RetryExecutionOptions,
    RetryIntent,
    RetryTargetInvalidError,
    consume_retry_intent,
    validate_retry_source,
)
from antcode_master.control.scheduler_authority import (
    SchedulerAuthorityLost,
    activate_scheduler_authority,
    claim_dispatch,
    dispatch_with_scheduler_epoch,
    require_scheduler_authority,
)
from antcode_master.control.scheduler_dispatch import dispatch_prepared_run
from antcode_master.control.scheduler_next_run import persist_next_run_time

# retry intent 数据结构与同事务校验/消费原语迁至 retry_intent_guard
# (P1-FN-05);这里 re-export 维持既有 import 路径(retry_loop / tests)。
from antcode_master.control.trigger_idempotency import (
    execute_idempotent_trigger,
    resume_idempotent_trigger,
)
from antcode_master.leader import ensure_leader, require_fencing_token

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
        self._durable_schedule = DurableScheduleRunner(self)

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
            token = await require_fencing_token()
            await activate_scheduler_authority(token)
            if not self.scheduler.running:
                self.scheduler.start()
            await self._load_active_tasks()
            self._durable_schedule.install_job()
            await self._durable_schedule.recover_due()
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
            if task.schedule_type in ONE_TIME_SCHEDULE_TYPES:
                await self._durable_schedule.register(task)
                return
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

            await persist_next_run_time(self.scheduler, task.id)
            logger.info(f"任务 {task.name} 已添加到调度器 (max_instances={self._task_max_instances(task)})")

        except Exception:
            logger.exception("添加任务失败")
            raise

    @staticmethod
    def _task_max_instances(task) -> int:
        return task_max_instances(task)

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

    async def trigger_task(
        self,
        task_id,
        idempotency_key: str | None = None,
        *,
        recovery_options: RecoveryExecutionOptions | None = None,
    ):
        """立即触发任务。P1-DB-01: 带 ``idempotency_key``（= outbox_id）时
        走确定性 run_id/job id 路径（``trigger_idempotency``），重放折叠到
        同一 run；无 key 的进程内直调路径保持旧行为。"""
        try:
            if idempotency_key:
                return await execute_idempotent_trigger(
                    self._execute_task,
                    self._resume_idempotent_trigger,
                    task_id,
                    idempotency_key=idempotency_key,
                    recovery_options=recovery_options,
                )
            if recovery_options is not None:
                raise ValueError("恢复执行必须使用 idempotency_key")
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
        "合法 busy"并使用更长延迟重排。
        """
        run_id = str(uuid.uuid5(RETRY_RUN_NAMESPACE, f"{intent.source_run_id}:{intent.retry_count}"))
        existing = await TaskRun.get_or_none(run_id=run_id)
        if existing is not None:
            validate_existing_retry_run(existing, intent)
            if not is_recoverable_retry_run(existing):
                return run_id
            async with self.concurrency_semaphore:
                return await self._resume_retry_run(intent, existing)
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

    async def _resume_retry_run(self, intent: RetryIntent, execution) -> str:
        return await resume_retry_run(self, intent, execution)

    def _create_trigger(self, task):
        """创建触发器"""
        return create_task_trigger(task)

    async def _execute_task(
        self,
        task_id,
        *,
        fixed_run_id: str | None = None,
        scheduler_fencing_token: int | None = None,
        recovery_options: RecoveryExecutionOptions | None = None,
    ):
        """执行任务的核心方法（带并发控制）"""
        async with self.concurrency_semaphore:
            return await self._execute_task_internal(
                task_id,
                fixed_run_id=fixed_run_id,
                scheduler_fencing_token=scheduler_fencing_token,
                recovery_options=recovery_options,
            )

    async def _resume_idempotent_trigger(self, task_id: int, execution) -> str:
        return await resume_idempotent_trigger(self, task_id, execution)

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
        scheduler_fencing_token: int | None = None,
        recovery_options: RecoveryExecutionOptions | None = None,
    ) -> str | None:
        """执行任务的内部实现（瘦壳）。

        把准备 / 分发 / 处理 / 失败 / 收尾 5 段拆到独立方法，主壳里只
        负责 trace 绑定 + 调度顺序。

        U1: 入口加 leader 闸口 —— 过去 leader 失效后 APScheduler 仍可能
        触发已排好的 job,这里直接放弃,避免两个 Master 同时跑同一个任务。
        """
        token = scheduler_fencing_token if scheduler_fencing_token is not None else await require_fencing_token()

        run_id = retry_options.run_id if retry_options is not None else (fixed_run_id or str(uuid.uuid4()))

        # P5.4: 在调度入口种入新的 W3C trace 上下文。
        trace_ids = new_trace()
        set_current_trace(trace_ids.traceparent)
        logger.debug(f"调度入口绑定 trace: task_id={task_id} run_id={run_id} traceparent={trace_ids.traceparent}")

        with self._track_execution():
            return await self._run_one_execution(
                task_id,
                run_id,
                retry_options,
                scheduler_fencing_token=token,
                recovery_options=recovery_options,
            )

    async def _run_one_execution(
        self,
        task_id,
        run_id,
        retry_options: RetryExecutionOptions | None = None,
        *,
        scheduler_fencing_token: int | None = None,
        recovery_options: RecoveryExecutionOptions | None = None,
    ) -> str | None:
        """Prepare a new run, then execute the shared dispatch pipeline."""
        token = scheduler_fencing_token if scheduler_fencing_token is not None else await require_fencing_token()
        prepared = await self._prepare_execution(
            task_id,
            run_id,
            retry_options,
            scheduler_fencing_token=token,
            recovery_options=recovery_options,
        )
        if prepared is None:
            return None
        return await self._run_prepared_execution(task_id, prepared)

    async def _run_prepared_execution(self, task_id, prepared) -> str:
        return await run_prepared_execution(self, task_id, prepared)

    async def _prepare_execution(
        self,
        task_id,
        run_id,
        retry_options: RetryExecutionOptions | None = None,
        *,
        scheduler_fencing_token: int | None = None,
        recovery_options: RecoveryExecutionOptions | None = None,
    ):
        """加载 task/project，做幂等校验，新建 TaskRun。

        返回 ``None`` 表示跳过执行（任务不存在 / 未激活 / 重复触发）。
        """
        task_info = await relation_service.get_task_with_project(task_id)
        if not task_info:
            logger.error(f"任务 {task_id} 不存在")
            if retry_options is not None:
                async with in_transaction("default") as conn:
                    await self._validate_retry_source(conn, task_id, retry_options)
                raise RetryTargetInvalidError(f"retry target task 不存在: task_id={task_id}")
            return None

        task = task_info["task"]
        project = task_info["project"]
        project_detail = task_info["project_detail"]

        token = scheduler_fencing_token if scheduler_fencing_token is not None else await require_fencing_token()
        claimed = await self._claim_task_run(
            task.id,
            run_id,
            retry_options,
            scheduler_fencing_token=token,
            recovery_options=recovery_options,
        )
        if claimed is None:
            return None
        task, execution = claimed

        current_running = self.task_execution_stats["currently_running"]
        logger.info(f"开始执行任务 {task.name} (当前并发: {current_running}/{settings.MAX_CONCURRENT_TASKS})")

        now = datetime.now(UTC)
        return task, project, project_detail, execution, now

    async def _claim_task_run(
        self,
        task_id: int,
        run_id: str,
        retry_options: RetryExecutionOptions | None = None,
        *,
        scheduler_fencing_token: int | None = None,
        recovery_options: RecoveryExecutionOptions | None = None,
    ):
        """Atomically claim an idle Task and create its TaskRun."""
        request = ExecutionClaimRequest(
            task_id=task_id,
            run_id=run_id,
            retry_options=retry_options,
            recovery_options=recovery_options,
            scheduler_fencing_token=scheduler_fencing_token,
        )
        dependencies = ExecutionClaimDependencies(
            task_model=Task,
            task_run_model=TaskRun,
            transaction_factory=in_transaction,
            require_fencing_token=require_fencing_token,
            require_scheduler_authority=require_scheduler_authority,
            lock_interrupted_source=lock_interrupted_source,
            transition_interrupted_source=transition_interrupted_source,
        )
        return await claim_task_run(self, request, dependencies)

    @staticmethod
    async def _count_active_runs(conn, task_id: int) -> int:
        return await count_active_runs(conn, task_id)

    @staticmethod
    async def _consume_retry_intent(conn, options: RetryExecutionOptions) -> None:
        """P1-FN-05: 与新 run 创建同事务清除 durable intent(见 retry_intent_guard)。"""
        await consume_retry_intent(conn, options)

    async def _validate_retry_source(self, conn, task_id: int, options: RetryExecutionOptions) -> None:
        await validate_retry_source(conn, task_id, options)

    async def _dispatch_and_run(self, task, project, project_detail, execution, run_id, now):
        """选 worker → 提交 → 拿单次执行 result dict。失败返回 dict。"""
        context = task, project, project_detail, execution, run_id, now, claim_dispatch
        return await dispatch_prepared_run(self, context)

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
            raise RuntimeError("派发失败必须通过 failure_settlement 原子结算")

        # success 分支
        if result.get("distributed") and result.get("pending"):
            await self._persist_result_fields(run_id, result)
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
            return None, True

        raise RuntimeError(
            f"Master 远程分发成功结果缺少 distributed=true,pending=true: run_id={run_id} result={result!r}"
        )

    @staticmethod
    async def _persist_result_fields(run_id, result):
        """在行锁内合并 result，避免陈旧 ORM 对象覆盖 Worker 结果。

        迁移 39 (``remove_task_run_log_file_paths``) 已下线 ``TaskRun``
        的 ``log_file_path`` / ``error_log_path`` 字段，任务日志改由
        ``task_logs`` 表承载，此处不再落磁盘路径。
        """
        await merge_dispatch_result_data(run_id, result)

    async def _finalize_stats(self, task, task_id, result_success, distributed_pending):
        """统计 success/failure，更新 next_run_time。"""
        if result_success is True:
            self.task_execution_stats["success_count"] += 1
        elif result_success is False and not distributed_pending:
            self.task_execution_stats["failed_count"] += 1

        if task:
            await persist_next_run_time(self.scheduler, task.id)

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

            result = await dispatch_with_scheduler_epoch(
                int(execution.scheduler_fencing_token),
                worker_task_dispatcher.dispatch_task,
                project_id=project.public_id,
                run_id=run_id,
                params=effective_execution_params(task.execution_params, execution.result_data),
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

        except (SchedulerAuthorityLost, SchedulerDispatchFenceError):
            raise
        except Exception as e:
            logger.exception("分布式执行任务失败")
            return {"success": False, "error": str(e)}

    async def _execute_rule_task(self, task, project, rule_detail, execution, *, target_worker):
        """提交规则任务，分页由 Worker 内的 UniversalRuleSpider 统一执行。"""
        try:
            if not rule_detail:
                return {"success": False, "error": "规则项目详情不存在"}

            params = effective_execution_params(task.execution_params, execution.result_data)
            params["scheduled_task_id"] = task.id
            params["scheduled_task_name"] = task.name
            result = await dispatch_with_scheduler_epoch(
                int(execution.scheduler_fencing_token),
                spider_task_dispatcher.submit_rule_task,
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

        except (SchedulerAuthorityLost, SchedulerDispatchFenceError):
            raise
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
        """按 Task -> source TaskRun 的统一锁顺序创建一次 retry intent。"""
        async with in_transaction("default") as conn:
            locked_task = await Task.filter(id=task.id).using_db(conn).select_for_update().first()
            if locked_task is None or int(locked_task.retry_count or 0) <= 0:
                return None
            current = await TaskRun.filter(run_id=execution.run_id).using_db(conn).select_for_update().first()
            if current is None:
                raise RuntimeError(f"retry source 不存在: run_id={execution.run_id}")
            if current.cancel_requested_at is not None:
                logger.info("run 已请求取消,不创建 retry intent: run_id={}", current.run_id)
                return None
            if current.next_retry_at is not None:
                return RetryIntent(task.id, current.run_id, current.retry_count, current.next_retry_at)
            if retry_was_decided(current):
                return None
            if int(current.retry_count or 0) >= int(locked_task.retry_count or 0):
                return None
            retry_count = int(current.retry_count or 0) + 1
            retry_delay = int(locked_task.retry_delay or settings.TASK_RETRY_DELAY)
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
        return RetryIntent(locked_task.id, current.run_id, retry_count, retry_time)

    async def _log_execution(self, execution, level, message):
        """记录执行日志到 ``task_logs`` 表（迁移 39 后不再落磁盘）。"""
        if not execution:
            return
        log_type = "stderr" if level.upper() in ("ERROR", "CRITICAL") else "stdout"
        await task_log_service.write_log(execution.run_id, log_type, f"[{level}] {message}")

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
