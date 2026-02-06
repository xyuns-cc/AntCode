"""
AntCode Core Package

共享核心包，包含：
- common: 通用模块（配置、日志、异常、ID生成、时间工具、安全）
- application: 应用层编排服务
- infrastructure: 基础设施适配（数据库、Redis、存储、可观测性）
- domain: 领域层（模型、Schema）
"""

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "common",
    "application",
    "infrastructure",
    "domain",
]


def __getattr__(name: str):
    if name in ("common", "application", "infrastructure", "domain"):
        import importlib

        module = importlib.import_module(f"antcode_core.{name}")
        globals()[name] = module
        return module
    raise AttributeError(f"module 'antcode_core' has no attribute '{name}'")
