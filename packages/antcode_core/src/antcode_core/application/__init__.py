"""
Application 模块

应用层编排服务（对外提供用例级接口）。
"""

__all__ = [
    "services",
]


def __getattr__(name: str):
    if name == "services":
        import importlib

        module = importlib.import_module("antcode_core.application.services")
        globals()[name] = module
        return module
    raise AttributeError(f"module 'antcode_core.application' has no attribute '{name}'")
