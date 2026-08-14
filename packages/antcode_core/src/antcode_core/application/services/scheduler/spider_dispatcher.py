"""爬虫任务调度器 - 通过 Redis Streams 将任务分发到工作节点"""

from __future__ import annotations

from loguru import logger

from antcode_core.application.services.scheduler.rule_dispatch_constraints import (
    resolve_rule_dispatch_constraints,
)
from antcode_core.application.services.workers.worker_dispatcher import worker_task_dispatcher

DEFAULT_TASK_TIMEOUT_SECONDS = 3600


class SpiderTaskDispatcher:
    """爬虫任务调度器，通过 Redis Streams 将任务分发到工作节点"""

    async def submit_rule_task(
        self,
        project,
        rule_detail,
        run_id,
        *,
        params=None,
        worker_id=None,
        timeout=DEFAULT_TASK_TIMEOUT_SECONDS,
        priority=None,
    ):
        """提交规则任务到工作节点。

        P16: params 结构必须与 UI 直调路径（web_api workers.py:1367）一致：
        ``rule_detail`` 塞进 ``params.kwargs``。否则 worker engine 里
        ``kwargs = params.get("kwargs", {}) if isinstance(params.get("kwargs", {}), dict) else params``
        取到空 dict（三元表达式选中 {} 因为 isinstance({}, dict)=True），
        RulePlugin.validate 报"规则任务缺少 target_url"，导致所有 scheduler
        触发路径下的 rule 任务全部失败。
        """
        outer_params: dict = dict(params or {})
        kwargs_dict = outer_params.get("kwargs")
        if not isinstance(kwargs_dict, dict):
            kwargs_dict = {}
        serialized_rule = self._serialize_rule_detail(rule_detail)
        constraints = resolve_rule_dispatch_constraints(rule_detail, serialized_rule)
        kwargs_dict["rule_detail"] = serialized_rule
        outer_params["kwargs"] = kwargs_dict
        runtime_env_name = self._runtime_env_name(project)

        result = await worker_task_dispatcher.dispatch_task(
            project_id=project.public_id,
            run_id=run_id,
            params=outer_params,
            runtime_env_name=runtime_env_name,
            project_type="rule",
            worker_id=worker_id,
            region=constraints.region,
            timeout=timeout,
            priority=priority,
            require_render=constraints.require_render,
        )

        if result.success:
            logger.info(f"任务已分发到节点 [{result.worker_name}]: {result.task_id}")
            return {
                "success": True,
                "task_id": result.task_id,
                "worker_id": result.worker_id,
                "worker_name": result.worker_name,
                "queue": "worker",
                "message": result.message or "任务已分发",
            }
        else:
            logger.error(f"任务分发失败: {result.error}")
            return {
                "success": False,
                "error": result.error or "分发失败",
                "worker_id": result.worker_id,
                "worker_name": result.worker_name,
            }

    @staticmethod
    def _runtime_env_name(project) -> str | None:
        if getattr(project, "env_location", None) != "worker":
            return None
        return getattr(project, "worker_env_name", None)

    def _serialize_rule_detail(self, rule_detail):
        """序列化规则详情"""
        serializer = getattr(rule_detail, "to_dispatch_dict", None)
        if not callable(serializer):
            raise TypeError("规则项目详情必须实现 to_dispatch_dict()")
        return serializer()


spider_task_dispatcher = SpiderTaskDispatcher()
