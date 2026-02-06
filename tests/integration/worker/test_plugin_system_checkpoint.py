"""
插件系统验证测试

Checkpoint 17: 验证插件系统
- 新增 plugin 不改 engine
- 各类型任务正确路由

Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7
"""

import pytest

from antcode_worker.domain.enums import TaskType
from antcode_worker.domain.models import ExecPlan, RunContext, TaskPayload
from antcode_worker.plugins.base import PluginBase
from antcode_worker.plugins.registry import PluginRegistry


class TestPluginRouting:
    """测试任务路由到正确的插件"""

    def setup_method(self):
        """每个测试前创建新的注册表"""
        self.registry = PluginRegistry()
        self.registry.load_builtin_plugins()

    def test_code_task_routes_to_code_plugin(self):
        """CODE 类型任务路由到 CodePlugin"""
        payload = TaskPayload(
            task_type=TaskType.CODE,
            entry_point="main.py",
        )
        plugin = self.registry.match(payload)
        assert plugin is not None
        assert plugin.name == "code"

    def test_spider_task_routes_to_spider_plugin(self):
        """SPIDER 类型任务路由到 SpiderPlugin"""
        payload = TaskPayload(
            task_type=TaskType.SPIDER,
            entry_point="spider.py",
        )
        plugin = self.registry.match(payload)
        assert plugin is not None
        assert plugin.name == "spider"

    def test_render_task_routes_to_render_plugin(self):
        """RENDER 类型任务路由到 RenderPlugin"""
        payload = TaskPayload(
            task_type=TaskType.RENDER,
            entry_point="template.html",
        )
        plugin = self.registry.match(payload)
        assert plugin is not None
        assert plugin.name == "render"

    def test_custom_task_no_match(self):
        """CUSTOM 类型任务没有匹配的内置插件"""
        payload = TaskPayload(
            task_type=TaskType.CUSTOM,
            entry_point="custom.py",
        )
        plugin = self.registry.match(payload)
        # 内置插件不处理 CUSTOM 类型
        assert plugin is None


class TestPluginPriority:
    """测试插件优先级"""

    def setup_method(self):
        self.registry = PluginRegistry()
        self.registry.load_builtin_plugins()

    def test_plugins_sorted_by_priority(self):
        """插件按优先级排序"""
        plugins = self.registry.list_plugins()
        priorities = [p["priority"] for p in plugins]
        # 应该是降序排列
        assert priorities == sorted(priorities, reverse=True)

    def test_spider_has_highest_priority(self):
        """Spider 插件优先级最高"""
        plugins = self.registry.list_plugins()
        assert plugins[0]["name"] == "spider"
        assert plugins[0]["priority"] == 20

    def test_render_has_medium_priority(self):
        """Render 插件优先级中等"""
        plugins = self.registry.list_plugins()
        render_plugin = next(p for p in plugins if p["name"] == "render")
        assert render_plugin["priority"] == 15

    def test_code_has_lowest_priority(self):
        """Code 插件优先级最低"""
        plugins = self.registry.list_plugins()
        code_plugin = next(p for p in plugins if p["name"] == "code")
        assert code_plugin["priority"] == 10


class CustomTestPlugin(PluginBase):
    """用于测试的自定义插件"""

    @property
    def name(self) -> str:
        return "custom_test"

    @property
    def priority(self) -> int:
        return 100  # 最高优先级

    def match(self, payload: TaskPayload) -> bool:
        return payload.task_type == TaskType.CUSTOM

    async def build_plan(
        self,
        context: RunContext,
        payload: TaskPayload,
    ) -> ExecPlan:
        return ExecPlan(
            command="python",
            args=[payload.entry_point],
            cwd=payload.project_path or ".",
            timeout_seconds=context.timeout_seconds,
        )


class TestAddPluginWithoutEngineChange:
    """测试新增插件不需要修改 engine"""

    def setup_method(self):
        self.registry = PluginRegistry()
        self.registry.load_builtin_plugins()

    def test_register_custom_plugin(self):
        """可以注册自定义插件"""
        custom_plugin = CustomTestPlugin()
        self.registry.register(custom_plugin)

        # 验证插件已注册
        plugins = self.registry.list_plugins()
        plugin_names = [p["name"] for p in plugins]
        assert "custom_test" in plugin_names

    def test_custom_plugin_matches_custom_task(self):
        """自定义插件匹配 CUSTOM 类型任务"""
        custom_plugin = CustomTestPlugin()
        self.registry.register(custom_plugin)

        payload = TaskPayload(
            task_type=TaskType.CUSTOM,
            entry_point="custom.py",
        )
        plugin = self.registry.match(payload)
        assert plugin is not None
        assert plugin.name == "custom_test"

    def test_custom_plugin_highest_priority(self):
        """自定义插件优先级最高时排在最前"""
        custom_plugin = CustomTestPlugin()
        self.registry.register(custom_plugin)

        plugins = self.registry.list_plugins()
        assert plugins[0]["name"] == "custom_test"
        assert plugins[0]["priority"] == 100

    @pytest.mark.asyncio
    async def test_custom_plugin_builds_plan(self):
        """自定义插件可以构建执行计划"""
        custom_plugin = CustomTestPlugin()
        self.registry.register(custom_plugin)

        context = RunContext(
            run_id="test-run-001",
            task_id="task-001",
            project_id="project-001",
            timeout_seconds=60,
        )
        payload = TaskPayload(
            task_type=TaskType.CUSTOM,
            entry_point="custom_script.py",
            project_path="/tmp/project",
        )

        plan = await self.registry.build_plan(context, payload)
        assert plan.command == "python"
        assert plan.args == ["custom_script.py"]
        assert plan.plugin_name == "custom_test"

    def test_unregister_plugin(self):
        """可以注销插件"""
        custom_plugin = CustomTestPlugin()
        self.registry.register(custom_plugin)

        # 验证已注册
        assert self.registry.get("custom_test") is not None

        # 注销
        result = self.registry.unregister("custom_test")
        assert result is True

        # 验证已注销
        assert self.registry.get("custom_test") is None


class TestPluginValidation:
    """测试插件验证功能"""

    def setup_method(self):
        self.registry = PluginRegistry()
        self.registry.load_builtin_plugins()

    def test_code_plugin_validates_entry_point(self):
        """CodePlugin 验证 entry_point 不能为空"""
        payload = TaskPayload(
            task_type=TaskType.CODE,
            entry_point="",  # 空的 entry_point
        )
        plugin = self.registry.match(payload)
        errors = plugin.validate(payload)
        assert len(errors) > 0
        assert "entry_point" in errors[0]

    def test_spider_plugin_validates_entry_point(self):
        """SpiderPlugin 验证 entry_point 不能为空"""
        payload = TaskPayload(
            task_type=TaskType.SPIDER,
            entry_point="",
        )
        plugin = self.registry.match(payload)
        errors = plugin.validate(payload)
        assert len(errors) > 0

    def test_render_plugin_validates_based_on_engine(self):
        """RenderPlugin 根据引擎类型验证"""
        # Jinja2 模式需要 entry_point
        payload = TaskPayload(
            task_type=TaskType.RENDER,
            entry_point="",
            kwargs={"engine": "jinja2"},
        )
        plugin = self.registry.match(payload)
        errors = plugin.validate(payload)
        assert len(errors) > 0

    def test_valid_payload_passes_validation(self):
        """有效的 payload 通过验证"""
        payload = TaskPayload(
            task_type=TaskType.CODE,
            entry_point="main.py",
        )
        plugin = self.registry.match(payload)
        errors = plugin.validate(payload)
        assert len(errors) == 0


class TestExecPlanGeneration:
    """测试执行计划生成"""

    def setup_method(self):
        self.registry = PluginRegistry()
        self.registry.load_builtin_plugins()

    @pytest.mark.asyncio
    async def test_code_plugin_generates_plan(self):
        """CodePlugin 生成正确的执行计划"""
        context = RunContext(
            run_id="test-run-001",
            task_id="task-001",
            project_id="project-001",
            timeout_seconds=120,
            memory_limit_mb=512,
        )
        payload = TaskPayload(
            task_type=TaskType.CODE,
            entry_point="main.py",
            args=["--verbose"],
            project_path="/tmp/project",
        )

        plan = await self.registry.build_plan(context, payload)

        assert plan.command.endswith("python") or "python" in plan.command
        assert "main.py" in plan.args
        assert "--verbose" in plan.args
        assert plan.timeout_seconds == 120
        assert plan.memory_limit_mb == 512
        assert plan.plugin_name == "code"

    @pytest.mark.asyncio
    async def test_spider_plugin_generates_scrapy_plan(self):
        """SpiderPlugin 生成 Scrapy 执行计划"""
        context = RunContext(
            run_id="test-run-002",
            task_id="task-002",
            project_id="project-002",
            timeout_seconds=300,
        )
        payload = TaskPayload(
            task_type=TaskType.SPIDER,
            entry_point="my_spider",
            project_path="/tmp/spider_project",
            kwargs={
                "framework": "scrapy",
                "output_file": "output.json",
            },
        )

        plan = await self.registry.build_plan(context, payload)

        assert "-m" in plan.args
        assert "scrapy" in plan.args
        assert "crawl" in plan.args
        assert "my_spider" in plan.args
        assert plan.plugin_name == "spider"

    @pytest.mark.asyncio
    async def test_spider_plugin_generates_script_plan(self):
        """SpiderPlugin 生成脚本模式执行计划"""
        context = RunContext(
            run_id="test-run-003",
            task_id="task-003",
            project_id="project-003",
        )
        payload = TaskPayload(
            task_type=TaskType.SPIDER,
            entry_point="spider_script.py",
            project_path="/tmp/spider_project",
            kwargs={
                "framework": "script",
                "log_level": "DEBUG",
            },
        )

        plan = await self.registry.build_plan(context, payload)

        assert "spider_script.py" in plan.args
        assert plan.env.get("SPIDER_LOG_LEVEL") == "DEBUG"
        assert plan.plugin_name == "spider"

    @pytest.mark.asyncio
    async def test_render_plugin_generates_template_plan(self):
        """RenderPlugin 生成模板渲染执行计划"""
        context = RunContext(
            run_id="test-run-004",
            task_id="task-004",
            project_id="project-004",
        )
        payload = TaskPayload(
            task_type=TaskType.RENDER,
            entry_point="template.html",
            project_path="/tmp/render_project",
            kwargs={
                "engine": "jinja2",
                "output_file": "output.html",
                "context_data": {"title": "Test"},
            },
        )

        plan = await self.registry.build_plan(context, payload)

        assert "-c" in plan.args  # 内联脚本模式
        assert "output.html" in plan.artifact_patterns
        assert plan.plugin_name == "render"

    @pytest.mark.asyncio
    async def test_render_plugin_generates_playwright_plan(self):
        """RenderPlugin 生成 Playwright 执行计划"""
        context = RunContext(
            run_id="test-run-005",
            task_id="task-005",
            project_id="project-005",
        )
        payload = TaskPayload(
            task_type=TaskType.RENDER,
            entry_point="https://example.com",
            kwargs={
                "engine": "playwright",
                "output_file": "screenshot.png",
                "screenshot": True,
            },
        )

        plan = await self.registry.build_plan(context, payload)

        assert "-c" in plan.args  # 内联脚本模式
        assert "screenshot.png" in plan.artifact_patterns
        assert plan.env.get("PLAYWRIGHT_BROWSERS_PATH") == "0"
        assert plan.plugin_name == "render"


class TestPluginIsolation:
    """测试插件隔离性 - 插件不应直接执行或网络请求"""

    def test_plugin_base_is_abstract(self):
        """PluginBase 是抽象类"""
        with pytest.raises(TypeError):
            PluginBase()  # type: ignore

    def test_plugin_only_returns_exec_plan(self):
        """插件只返回 ExecPlan，不执行任何操作"""
        from antcode_worker.plugins.code.plugin import CodePlugin

        plugin = CodePlugin()

        # 验证插件没有执行相关的方法
        assert not hasattr(plugin, "execute")
        assert not hasattr(plugin, "run")
        assert not hasattr(plugin, "send")
        assert not hasattr(plugin, "report")

        # 只有这些方法
        assert hasattr(plugin, "match")
        assert hasattr(plugin, "build_plan")
        assert hasattr(plugin, "validate")


class TestEnginePluginIntegration:
    """测试 Engine 与 Plugin 的集成"""

    def test_engine_accepts_plugin_registry(self):
        """Engine 接受 PluginRegistry 作为参数"""
        from antcode_worker.engine.engine import Engine

        registry = PluginRegistry()
        registry.load_builtin_plugins()

        # Engine 构造函数接受 plugin_registry 参数
        # 这验证了 Engine 不需要修改就能使用不同的插件配置
        engine = Engine(
            transport=None,
            executor=None,
            plugin_registry=registry,
        )

        assert engine._plugin_registry is registry

    def test_engine_does_not_hardcode_plugins(self):
        """Engine 不硬编码任何插件"""
        from antcode_worker.engine.engine import Engine
        import inspect

        source = inspect.getsource(Engine)

        # Engine 源码中不应该直接引用具体的插件类
        assert "CodePlugin" not in source
        assert "SpiderPlugin" not in source
        assert "RenderPlugin" not in source

    def test_registry_is_injectable(self):
        """PluginRegistry 是可注入的"""
        from antcode_worker.engine.engine import Engine

        # 可以注入空的 registry
        empty_registry = PluginRegistry()
        engine1 = Engine(
            transport=None,
            executor=None,
            plugin_registry=empty_registry,
        )
        assert len(engine1._plugin_registry.list_plugins()) == 0

        # 可以注入带插件的 registry
        full_registry = PluginRegistry()
        full_registry.load_builtin_plugins()
        engine2 = Engine(
            transport=None,
            executor=None,
            plugin_registry=full_registry,
        )
        assert len(engine2._plugin_registry.list_plugins()) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
