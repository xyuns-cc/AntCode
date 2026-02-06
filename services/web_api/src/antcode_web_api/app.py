"""
FastAPI 应用实例

提供延迟初始化的 FastAPI 应用
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

_app: "FastAPI | None" = None


def get_app() -> "FastAPI":
    """获取或创建 FastAPI 应用实例"""
    global _app
    if _app is None:
        from antcode_web_api.app_factory import create_app

        _app = create_app()
    return _app


# 延迟导出 app 实例
def __getattr__(name: str):
    """延迟属性访问"""
    if name == "app":
        return get_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
