"""爬虫任务调度器 - 通过 Redis Streams 分发任务"""

from __future__ import annotations

from antcode_core.application.services.workers.worker_dispatcher import worker_task_dispatcher
from loguru import logger


class SpiderTaskDispatcher:
    """爬虫任务调度器，通过 Redis Streams 分发任务"""

    async def submit_rule_task(
        self,
        project,
        rule_detail,
        run_id,
        params=None,
        worker_id=None,
    ):
        """提交规则任务到工作节点。

        P16: params 结构必须与 UI 触发路径（workers.py:1367
        ``dispatch_task_to_worker``）一致——``rule_detail`` 塞进
        ``params.kwargs``，否则 worker 侧 engine._build_task_payload
        走 ``kwargs = params.get("kwargs", {})`` 拿到空 dict，
        RulePlugin.validate 报 "缺 target_url / extraction_rules"，
        所有 scheduler 触发的 rule 任务全部失败。
        """
        outer_params: dict = dict(params or {})
        kwargs_dict = outer_params.get("kwargs")
        if not isinstance(kwargs_dict, dict):
            kwargs_dict = {}
        kwargs_dict["rule_detail"] = self._serialize_rule_detail(rule_detail)
        outer_params["kwargs"] = kwargs_dict

        result = await worker_task_dispatcher.dispatch_task(
            project_id=project.public_id,
            run_id=run_id,
            params=outer_params,
            project_type="rule",
            worker_id=worker_id,
        )

        if result.success:
            logger.info(f"任务已分发到 Worker [{result.worker_name}]: {result.task_id}")
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

    async def submit_batch_tasks(
        self,
        project,
        rule_details,
        run_id,
        params=None,
        worker_id=None,
    ):
        """批量提交任务到工作节点"""
        tasks = []
        for i, rule_detail in enumerate(rule_details):
            task_item = {
                "task_id": f"{run_id}-{i}",
                "project_id": project.public_id,
                "project_type": "rule",
                "params": {
                    "rule_detail": self._serialize_rule_detail(rule_detail),
                    **(params or {}),
                },
            }
            tasks.append(task_item)

        return await worker_task_dispatcher.dispatch_batch(
            tasks=tasks,
            worker_id=worker_id,
        )

    def _serialize_rule_detail(self, rule_detail):
        """序列化规则详情"""
        serializer = getattr(rule_detail, "to_dispatch_dict", None)
        if not callable(serializer):
            raise TypeError("规则项目详情必须实现 to_dispatch_dict()")
        return serializer()


spider_task_dispatcher = SpiderTaskDispatcher()
