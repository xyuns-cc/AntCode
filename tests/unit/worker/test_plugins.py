"""
Worker 插件系统单元测试

测试插件注册和基类功能
"""

import pytest

from antcode_worker.plugins.base import PluginBase
from antcode_worker.plugins.registry import PluginRegistry
from antcode_worker.domain.enums import TaskType
from antcode_worker.domain.models import TaskPayload


class TestPluginBase:
    """插件基类测试"""

    def test_plugin_base_is_abstract(self):
        """测试基类是抽象类"""
        # 不能直接实例化
        with pytest.raises(TypeError):
            PluginBase()

    def test_plugin_default_priority(self):
        """测试默认优先级"""
        class TestPlugin(PluginBase):
            @property
            def name(self):
                return "test"
            
            def match(self, payload):
                return True
            
            async def build_plan(self, context, payload):
                return None
        
        plugin = TestPlugin()
        assert plugin.priority == 0


class TestPluginRegistry:
    """插件注册表测试"""

    def test_registry_creation(self):
        """测试创建注册表"""
        registry = PluginRegistry()
        
        assert registry is not None
        assert len(registry.list_plugins()) >= 0

    def test_registry_get_nonexistent(self):
        """测试获取不存在的插件"""
        registry = PluginRegistry()
        
        plugin = registry.get("nonexistent-plugin")
        assert plugin is None

    def test_registry_list_plugins(self):
        """测试列出插件"""
        registry = PluginRegistry()
        
        plugins = registry.list_plugins()
        assert isinstance(plugins, list)
