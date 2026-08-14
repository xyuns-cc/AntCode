"""根据执行策略确定任务执行 Worker。"""

from loguru import logger

from antcode_core.application.services.scheduler.execution_constraints import (
    NO_RULE_DISPATCH_CONSTRAINTS,
    ResolutionContext,
    require_worker_constraints,
    required_task_type,
)
from antcode_core.application.services.scheduler.rule_dispatch_constraints import (
    RuleDispatchConstraints,
)
from antcode_core.application.services.workers.worker_registration_gate import (
    has_unacknowledged_v2_registration,
)
from antcode_core.common.exceptions import WorkerUnavailableError
from antcode_core.domain.models import User, Worker, WorkerStatus
from antcode_core.domain.models.enums import ExecutionStrategy


class ExecutionResolver:
    """执行 Worker 解析器"""

    async def resolve_execution_worker(
        self,
        task,
        project,
        *,
        constraints: RuleDispatchConstraints | None = None,
    ):
        """
        解析任务应该在哪个 Worker 执行

        Args:
            task: 调度任务
            project: 关联的项目

        Returns:
            (worker, strategy): 执行 Worker 和实际使用的策略

        Raises:
            WorkerUnavailableError: 当无可用 Worker 时
        """
        constraints = constraints or NO_RULE_DISPATCH_CONSTRAINTS
        strategy = self._get_effective_strategy(task, project)
        required_type = required_task_type(project)
        logger.debug(f"任务 {task.name} 使用执行策略: {strategy}")
        context = ResolutionContext(
            task=task,
            project=project,
            strategy=strategy,
            constraints=constraints,
            required_task_type=required_type,
        )
        worker = await self._resolve_for_strategy(context)
        await self._require_worker_use_access(task, worker)
        await require_worker_constraints(
            worker,
            constraints=constraints,
            required_type=required_type,
        )
        return worker, strategy.value

    async def _resolve_for_strategy(self, context: ResolutionContext):
        if context.strategy == ExecutionStrategy.FIXED_WORKER:
            return await self._resolve_fixed_worker(context.project)
        if context.strategy == ExecutionStrategy.SPECIFIED:
            return await self._resolve_specified_worker(context.task)
        if context.strategy == ExecutionStrategy.AUTO_SELECT:
            worker = await self._resolve_auto_select(
                context.project,
                task=context.task,
                constraints=context.constraints,
                required_task_type=context.required_task_type,
            )
        elif context.strategy == ExecutionStrategy.PREFER_BOUND:
            worker = await self._resolve_prefer_bound(
                context.project,
                task=context.task,
                constraints=context.constraints,
                required_task_type=context.required_task_type,
            )
        else:
            raise ValueError(f"Unknown execution strategy: {context.strategy}")
        if worker is None:
            raise WorkerUnavailableError("No available Worker for task execution")
        return worker

    def _get_effective_strategy(self, task, project):
        """获取实际使用的执行策略（任务级别 > 项目级别 > 默认）"""
        if task.execution_strategy:
            return task.execution_strategy
        if project.execution_strategy:
            return project.execution_strategy
        return ExecutionStrategy.AUTO_SELECT

    async def _ensure_worker_online(self, worker: Worker) -> bool:
        """确保 Worker 在线并刷新心跳状态。"""
        if await has_unacknowledged_v2_registration(worker.public_id):
            return False
        if worker.status == WorkerStatus.ONLINE:
            return True

        try:
            from antcode_core.application.services.workers.worker_heartbeat_service import (
                worker_heartbeat_service,
            )

            is_online = await worker_heartbeat_service.manual_test_worker(worker.id)
            if is_online:
                latest = await Worker.get_or_none(id=worker.id)
                if latest:
                    worker.status = latest.status
                    worker.last_heartbeat = latest.last_heartbeat
            return is_online
        except Exception as e:
            raise WorkerUnavailableError(
                f"Worker 在线检测失败: {e}",
                str(worker.id),
            ) from e

    async def _resolve_fixed_worker(self, project):
        """解析固定 Worker 策略"""
        if not project.bound_worker_id:
            raise WorkerUnavailableError("项目未绑定执行 Worker")

        worker = await Worker.get_or_none(id=project.bound_worker_id)
        if not worker:
            raise WorkerUnavailableError(
                f"绑定 Worker 不存在 (id={project.bound_worker_id})",
                str(project.bound_worker_id),
            )

        runtime_worker_id = getattr(project, "worker_id", None)
        if runtime_worker_id and worker.public_id != runtime_worker_id:
            raise WorkerUnavailableError(
                "固定 Worker 与项目运行时 Worker 不一致",
                str(worker.id),
            )

        if not await self._ensure_worker_online(worker):
            raise WorkerUnavailableError(f"绑定 Worker [{worker.name}] 离线", str(worker.id))

        logger.info(f"FIXED_WORKER: 使用 Worker [{worker.name}]")
        return worker

    async def _resolve_specified_worker(self, task):
        """解析指定 Worker 策略"""
        if not task.specified_worker_id:
            raise WorkerUnavailableError("任务未指定执行 Worker")

        worker = await Worker.get_or_none(id=task.specified_worker_id)
        if not worker:
            raise WorkerUnavailableError(
                f"指定 Worker 不存在 (id={task.specified_worker_id})",
                str(task.specified_worker_id),
            )

        if not await self._ensure_worker_online(worker):
            raise WorkerUnavailableError(f"指定 Worker [{worker.name}] 离线", str(worker.id))

        logger.info(f"SPECIFIED: 使用 Worker [{worker.name}]")
        return worker

    async def _resolve_auto_select(
        self,
        project,
        exclude_workers=None,
        *,
        task=None,
        constraints=NO_RULE_DISPATCH_CONSTRAINTS,
        required_task_type=None,
    ):
        """解析自动选择策略"""
        from antcode_core.application.services.workers import worker_load_balancer

        candidates = await self._usable_workers(task) if task is not None else None
        if candidates is not None and constraints.region:
            candidates = [worker for worker in candidates if worker.region == constraints.region]
        best_worker = await worker_load_balancer.select_best_worker(
            workers=candidates,
            exclude_workers=exclude_workers,
            region=constraints.region,
            require_render=constraints.require_render,
            require_task_type=required_task_type,
        )

        if not best_worker:
            logger.warning("AUTO_SELECT: 无可用 Worker")
            return None

        logger.info(f"AUTO_SELECT: 选中 Worker [{best_worker.name}]")
        return best_worker

    async def _resolve_prefer_bound(
        self,
        project,
        *,
        task=None,
        constraints=NO_RULE_DISPATCH_CONSTRAINTS,
        required_task_type=None,
    ):
        """解析优先绑定 Worker 策略"""
        # 尝试使用绑定 Worker
        if project.bound_worker_id:
            worker = await Worker.get_or_none(id=project.bound_worker_id)
            if worker is not None and await self._preferred_worker_is_usable(
                worker,
                task=task,
                constraints=constraints,
                required_task_type=required_task_type,
            ):
                logger.info(f"PREFER_BOUND: 使用绑定 Worker [{worker.name}]")
                return worker

            if worker:
                logger.warning(f"PREFER_BOUND: 绑定 Worker [{worker.name}] 离线")
            else:
                logger.warning(f"PREFER_BOUND: 绑定 Worker 不存在 (id={project.bound_worker_id})")

        logger.info("PREFER_BOUND: 绑定 Worker 不可用，自动选择 Worker")
        exclude_workers = [project.bound_worker_id] if project.bound_worker_id else None
        return await self._resolve_auto_select(
            project,
            exclude_workers,
            task=task,
            constraints=constraints,
            required_task_type=required_task_type,
        )

    async def _preferred_worker_is_usable(
        self,
        worker,
        *,
        task,
        constraints,
        required_task_type,
    ) -> bool:
        if worker is None:
            return False
        if task is not None and not await self._has_worker_use_access(task, worker):
            return False
        if not await self._ensure_worker_online(worker):
            return False
        try:
            await require_worker_constraints(
                worker,
                constraints=constraints,
                required_type=required_task_type,
            )
        except WorkerUnavailableError as exc:
            logger.warning(f"PREFER_BOUND: 绑定 Worker 不满足规则约束: {exc.message}")
            return False
        return True

    async def _usable_workers(self, task):
        from antcode_core.application.services.workers import worker_service

        user_id = self._task_owner_id(task)
        is_admin = await User.filter(id=user_id, is_admin=True).exists()
        return await worker_service.get_user_workers(
            user_id,
            is_admin=is_admin,
            required_permission="use",
        )

    async def _has_worker_use_access(self, task, worker: Worker) -> bool:
        from antcode_core.application.services.workers import worker_service

        user_id = self._task_owner_id(task)
        is_admin = await User.filter(id=user_id, is_admin=True).exists()
        return await worker_service.check_user_worker_permission(
            user_id,
            worker.id,
            is_admin=is_admin,
            required_permission="use",
        )

    async def _require_worker_use_access(self, task, worker: Worker) -> None:
        if not await self._has_worker_use_access(task, worker):
            raise WorkerUnavailableError("任务所有者没有目标 Worker 的 use 权限", str(worker.id))

    @staticmethod
    def _task_owner_id(task) -> int:
        user_id = getattr(task, "user_id", None)
        if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id < 1:
            raise WorkerUnavailableError("任务缺少有效所有者，拒绝选择 Worker")
        return user_id


# 全局实例
execution_resolver = ExecutionResolver()
