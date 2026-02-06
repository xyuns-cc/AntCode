"""
插件注册表

Requirements: 8.1
"""


from loguru import logger

from antcode_worker.domain.errors import PluginError
from antcode_worker.domain.models import ExecPlan, RunContext, TaskPayload
from antcode_worker.plugins.base import PluginBase


class PluginRegistry:
    """
    插件注册表

    负责插件的发现、注册和路由。

    Requirements: 8.1
    """

    def __init__(self):
        self._plugins: list[PluginBase] = []

    def register(self, plugin: PluginBase) -> None:
        """注册插件"""
        self._plugins.append(plugin)
        # 按优先级排序
        self._plugins.sort(key=lambda p: -p.priority)
        logger.info(f"插件已注册: {plugin.name}")

    def unregister(self, name: str) -> bool:
        """注销插件"""
        for i, p in enumerate(self._plugins):
            if p.name == name:
                self._plugins.pop(i)
                logger.info(f"插件已注销: {name}")
                return True
        return False

    def get(self, name: str) -> PluginBase | None:
        """获取插件"""
        for p in self._plugins:
            if p.name == name:
                return p
        return None

    def match(self, payload: TaskPayload) -> PluginBase | None:
        """匹配插件"""
        for plugin in self._plugins:
            if plugin.match(payload):
                return plugin
        return None

    async def build_plan(
        self,
        context: RunContext,
        payload: TaskPayload,
    ) -> ExecPlan:
        """
        构建执行计划

        自动匹配插件并生成执行计划。
        """
        plugin = self.match(payload)
        if not plugin:
            raise PluginError(
                f"没有匹配的插件: task_type={payload.task_type}",
            )

        # 验证
        errors = plugin.validate(payload)
        if errors:
            raise PluginError(
                f"任务验证失败: {', '.join(errors)}",
                plugin_name=plugin.name,
            )

        # 构建计划
        plan = await plugin.build_plan(context, payload)
        plan.plugin_name = plugin.name

        logger.debug(f"执行计划已生成: plugin={plugin.name}")
        return plan

    def list_plugins(self) -> list[dict]:
        """列出所有插件"""
        return [
            {"name": p.name, "priority": p.priority}
            for p in self._plugins
        ]

    def load_builtin_plugins(self) -> None:
        """加载内置插件"""
        # Code Plugin
        try:
            from antcode_worker.plugins.code.plugin import CodePlugin
            self.register(CodePlugin())
        except ImportError:
            logger.warning("CodePlugin 加载失败")

        # Spider Plugin
        try:
            from antcode_worker.plugins.spider.plugin import SpiderPlugin
            self.register(SpiderPlugin())
        except ImportError:
            logger.warning("SpiderPlugin 加载失败")

        # Render Plugin
        try:
            from antcode_worker.plugins.render.plugin import RenderPlugin
            self.register(RenderPlugin())
        except ImportError:
            logger.warning("RenderPlugin 加载失败")
