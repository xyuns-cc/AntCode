"""
插件系统

插件只产出 ExecPlan，不直接执行进程、不直接网络上报。

Requirements: 8.1, 8.2
"""

from antcode_worker.plugins.base import PluginBase
from antcode_worker.plugins.registry import PluginRegistry

__all__ = ["PluginBase", "PluginRegistry"]
