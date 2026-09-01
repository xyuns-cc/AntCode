"""插件注册表：插件的发现、注册与路由。"""

from loguru import logger

from antcode_worker.domain.errors import PluginError
from antcode_worker.domain.models import ExecPlan, RunContext, TaskPayload
from antcode_worker.plugins.base import PluginBase


class PluginRegistry:
    def __init__(self):
        self._plugins: list[PluginBase] = []

    def register(self, plugin: PluginBase) -> None:
        self._plugins.append(plugin)
        self._plugins.sort(key=lambda p: -p.priority)
        logger.info(f"插件已注册: {plugin.name}")

    def unregister(self, name: str) -> bool:
        for i, p in enumerate(self._plugins):
            if p.name == name:
                self._plugins.pop(i)
                logger.info(f"插件已注销: {name}")
                return True
        return False

    def get(self, name: str) -> PluginBase | None:
        for p in self._plugins:
            if p.name == name:
                return p
        return None

    def match(self, payload: TaskPayload) -> PluginBase | None:
        for plugin in self._plugins:
            if plugin.match(payload):
                return plugin
        return None

    async def build_plan(
        self,
        context: RunContext,
        payload: TaskPayload,
    ) -> ExecPlan:
        """自动匹配插件并生成执行计划。"""
        plugin = self.match(payload)
        if not plugin:
            raise PluginError(
                f"没有匹配的插件: task_type={payload.task_type}",
            )

        errors = plugin.validate(payload)
        if errors:
            raise PluginError(
                f"任务验证失败: {', '.join(errors)}",
                plugin_name=plugin.name,
            )

        plan = await plugin.build_plan(context, payload)
        plan.plugin_name = plugin.name

        logger.debug(f"执行计划已生成: plugin={plugin.name}")
        return plan

    def list_plugins(self) -> list[dict]:
        return [{"name": p.name, "priority": p.priority} for p in self._plugins]

    def load_builtin_plugins(self) -> None:
        try:
            from antcode_worker.plugins.code.plugin import CodePlugin

            self.register(CodePlugin())
        except ImportError:
            logger.warning("CodePlugin 加载失败")

        try:
            from antcode_worker.plugins.spider.plugin import SpiderPlugin

            self.register(SpiderPlugin())
        except ImportError:
            logger.warning("SpiderPlugin 加载失败")

        try:
            from antcode_worker.plugins.render.plugin import RenderPlugin

            self.register(RenderPlugin())
        except ImportError:
            logger.warning("RenderPlugin 加载失败")

        # WORKER_ENABLE_RULE_PLUGIN=false 时不加载，让 worker 变成 "code-only worker"。
        # 默认开（当前主用途是爬虫）。
        import os as _os

        rule_enabled = _os.environ.get("WORKER_ENABLE_RULE_PLUGIN", "true").strip().lower() not in (
            "false",
            "0",
            "no",
            "off",
        )
        if rule_enabled:
            try:
                from antcode_worker.plugins.rule.plugin import RulePlugin

                self.register(RulePlugin())
            except ImportError as exc:
                logger.warning(f"RulePlugin 加载失败（scrapy/依赖未装？）: {exc}")
        else:
            logger.info("WORKER_ENABLE_RULE_PLUGIN=false，跳过 RulePlugin 注册")

    def capabilities(self) -> list[str]:
        """当前 worker 能处理的 task_type，由 register/heartbeat 上报给 master 做能力路由
        （避免把 rule 派到关掉了 RulePlugin 的 worker）。"""
        return [p.name for p in self._plugins]
