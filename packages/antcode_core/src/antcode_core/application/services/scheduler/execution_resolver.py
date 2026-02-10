"""
执行 Worker 解析器 - 根据执行策略确定任务执行 Worker

支持的执行策略：
- FIXED_WORKER: 固定 Worker（仅在绑定 Worker 执行，不可用时失败）
- SPECIFIED: 指定 Worker（任务级别指定）
- AUTO_SELECT: 自动选择（负载均衡）
- PREFER_BOUND: 优先绑定 Worker（不可用时自动选择其他 Worker）
"""

from loguru import logger

from antcode_core.common.exceptions import WorkerUnavailableError
from antcode_core.domain.models import Worker, WorkerStatus
from antcode_core.domain.models.enums import ExecutionStrategy


class ExecutionResolver:
    """执行 Worker 解析器"""

    async def resolve_execution_worker(self, task, project):
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
        # 确定实际使用的执行策略（任务级别覆盖项目级别）
        strategy = self._get_effective_strategy(task, project)

        logger.debug(f"任务 {task.name} 使用执行策略: {strategy}")

        if strategy == ExecutionStrategy.FIXED_WORKER:
            return await self._resolve_fixed_worker(project), strategy.value

        elif strategy == ExecutionStrategy.SPECIFIED:
            return await self._resolve_specified_worker(task), strategy.value

        elif strategy == ExecutionStrategy.AUTO_SELECT:
            worker = await self._resolve_auto_select(project)
            if not worker:
                raise WorkerUnavailableError("No available Worker for task execution")
            return worker, strategy.value

        elif strategy == ExecutionStrategy.PREFER_BOUND:
            worker = await self._resolve_prefer_bound(project)
            if not worker:
                raise WorkerUnavailableError("No available Worker for task execution")
            return worker, strategy.value

        else:
            raise ValueError(f"Unknown execution strategy: {strategy}")

    def _get_effective_strategy(self, task, project):
        """获取实际使用的执行策略（任务级别 > 项目级别 > 默认）"""
        if task.execution_strategy:
            return task.execution_strategy
        if project.execution_strategy:
            return project.execution_strategy
        return ExecutionStrategy.AUTO_SELECT

    async def _ensure_worker_online(self, worker: Worker) -> bool:
        """确保 Worker 在线（含心跳兜底检测）。"""
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
            logger.debug(f"Worker 在线检测失败，回退到状态判断: worker={worker.id}, error={e}")
            latest = await Worker.get_or_none(id=worker.id)
            return bool(latest and latest.status == WorkerStatus.ONLINE)

    async def _resolve_fixed_worker(self, project):
        """解析固定 Worker 策略"""
        if not project.bound_worker_id:
            raise WorkerUnavailableError("项目未绑定执行 Worker")

        worker = await Worker.get_or_none(id=project.bound_worker_id)
        if not worker:
            raise WorkerUnavailableError(
                f"绑定 Worker 不存在 (id={project.bound_worker_id})", project.bound_worker_id
            )

        if not await self._ensure_worker_online(worker):
            raise WorkerUnavailableError(f"绑定 Worker [{worker.name}] 离线", worker.id)

        logger.info(f"FIXED_WORKER: 使用 Worker [{worker.name}]")
        return worker

    async def _resolve_specified_worker(self, task):
        """解析指定 Worker 策略"""
        if not task.specified_worker_id:
            raise WorkerUnavailableError("任务未指定执行 Worker")

        worker = await Worker.get_or_none(id=task.specified_worker_id)
        if not worker:
            raise WorkerUnavailableError(
                f"指定 Worker 不存在 (id={task.specified_worker_id})", task.specified_worker_id
            )

        if not await self._ensure_worker_online(worker):
            raise WorkerUnavailableError(f"指定 Worker [{worker.name}] 离线", worker.id)

        logger.info(f"SPECIFIED: 使用 Worker [{worker.name}]")
        return worker

    async def _resolve_auto_select(self, project, exclude_workers=None):
        """解析自动选择策略"""
        from antcode_core.application.services.workers import worker_load_balancer

        require_render = await self._check_render_requirement(project)
        best_worker = await worker_load_balancer.select_best_worker(
            exclude_workers=exclude_workers,
            require_render=require_render,
        )

        if not best_worker:
            logger.warning("AUTO_SELECT: 无可用 Worker")
            return None

        logger.info(f"AUTO_SELECT: 选中 Worker [{best_worker.name}]")
        return best_worker

    async def _resolve_prefer_bound(self, project):
        """解析优先绑定 Worker 策略"""
        # 尝试使用绑定 Worker
        if project.bound_worker_id:
            worker = await Worker.get_or_none(id=project.bound_worker_id)
            if worker and await self._ensure_worker_online(worker):
                logger.info(f"PREFER_BOUND: 使用绑定 Worker [{worker.name}]")
                return worker

            if worker:
                logger.warning(f"PREFER_BOUND: 绑定 Worker [{worker.name}] 离线")
            else:
                logger.warning(f"PREFER_BOUND: 绑定 Worker 不存在 (id={project.bound_worker_id})")

        # 检查故障转移
        if not project.fallback_enabled and project.bound_worker_id:
            raise WorkerUnavailableError(
                "绑定 Worker 不可用且未启用故障转移", project.bound_worker_id
            )

        # 故障转移：自动选择其他 Worker
        logger.info("PREFER_BOUND: 故障转移，自动选择 Worker")
        exclude_workers = [project.bound_worker_id] if project.bound_worker_id else None
        return await self._resolve_auto_select(project, exclude_workers)

    async def _check_render_requirement(self, project):
        """检查项目是否需要渲染能力"""
        from antcode_core.domain.models.enums import CrawlEngine
        from antcode_core.domain.models.project import ProjectRule

        # 规则项目：检查是否使用浏览器引擎
        if project.type.value == "rule":
            rule = await ProjectRule.get_or_none(project_id=project.id)
            if rule and rule.engine == CrawlEngine.BROWSER:
                return True

        return False


# 全局实例
execution_resolver = ExecutionResolver()
