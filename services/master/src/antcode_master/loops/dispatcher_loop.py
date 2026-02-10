"""爬虫任务调度器 - 通过 Redis Streams 分发任务"""

from __future__ import annotations

from loguru import logger

from antcode_core.application.services.workers.worker_dispatcher import worker_task_dispatcher


class SpiderTaskDispatcher:
    """爬虫任务调度器，通过 Redis Streams 分发任务"""

    async def submit_rule_task(
        self,
        project,
        rule_detail,
        run_id,
        params=None,
        worker_id=None,
        require_render=False,
    ):
        """提交规则任务到工作节点"""
        if hasattr(rule_detail, "engine"):
            engine = rule_detail.engine
            if hasattr(engine, "value"):
                engine = engine.value
            if engine == "browser":
                require_render = True

        task_params = {
            "rule_detail": self._serialize_rule_detail(rule_detail),
            **(params or {}),
        }

        result = await worker_task_dispatcher.dispatch_task(
            project_id=project.public_id,
            run_id=run_id,
            params=task_params,
            project_type="rule",
            require_render=require_render,
            worker_id=worker_id,
        )

        if result.get("success"):
            logger.info(f"任务已分发到 Worker [{result.get('worker_name')}]: {result.get('task_id')}")
            return {
                "success": True,
                "task_id": result.get("task_id"),
                "worker_id": result.get("worker_id"),
                "worker_name": result.get("worker_name"),
                "queue": "worker",
                "message": result.get("message", "任务已分发"),
            }
        else:
            logger.error(f"任务分发失败: {result.get('error')}")
            return {
                "success": False,
                "error": result.get("error", "分发失败"),
                "worker_id": result.get("worker_id"),
                "worker_name": result.get("worker_name"),
            }

    async def submit_batch_tasks(
        self,
        project,
        rule_details,
        run_id,
        params=None,
        worker_id=None,
        require_render=False,
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
                "require_render": require_render,
            }
            tasks.append(task_item)

        return await worker_task_dispatcher.dispatch_batch(
            tasks=tasks,
            worker_id=worker_id,
            require_render=require_render,
        )

    def _serialize_rule_detail(self, rule_detail):
        """序列化规则详情"""
        data = {
            "target_url": rule_detail.target_url,
            "callback_type": rule_detail.callback_type.value
            if hasattr(rule_detail.callback_type, "value")
            else rule_detail.callback_type,
            "request_method": rule_detail.request_method.value
            if hasattr(rule_detail.request_method, "value")
            else rule_detail.request_method,
            "engine": rule_detail.engine.value
            if hasattr(rule_detail.engine, "value")
            else rule_detail.engine,
            "headers": rule_detail.headers or {},
            "cookies": rule_detail.cookies or {},
            "priority": rule_detail.priority or 0,
            "dont_filter": getattr(rule_detail, "dont_filter", False),
        }

        if rule_detail.request_body:
            data["request_body"] = rule_detail.request_body
        if rule_detail.proxy_config:
            data["proxy_config"] = rule_detail.proxy_config
        if hasattr(rule_detail, "extraction_rules") and rule_detail.extraction_rules:
            data["extraction_rules"] = rule_detail.extraction_rules
        if hasattr(rule_detail, "pagination_config") and rule_detail.pagination_config:
            data["pagination_config"] = rule_detail.pagination_config
        if hasattr(rule_detail, "wait_time") and rule_detail.wait_time:
            data["wait_time"] = rule_detail.wait_time
        if hasattr(rule_detail, "javascript_code") and rule_detail.javascript_code:
            data["javascript_code"] = rule_detail.javascript_code

        return data


spider_task_dispatcher = SpiderTaskDispatcher()
