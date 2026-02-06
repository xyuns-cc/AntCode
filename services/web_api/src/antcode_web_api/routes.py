"""路由注册模块"""

from fastapi import FastAPI

from antcode_web_api.routes import api_router


def register_routes(app: FastAPI) -> None:
    """注册所有 API 路由"""
    app.include_router(api_router, prefix="/api")
