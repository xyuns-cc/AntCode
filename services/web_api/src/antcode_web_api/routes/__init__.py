from fastapi import APIRouter, FastAPI

from antcode_web_api.routes.v1 import v1_router

api_router = APIRouter()
api_router.include_router(v1_router, prefix="/v1")


def register_routes(app: FastAPI) -> None:
    """注册所有 API 路由"""
    app.include_router(api_router, prefix="/api")


__all__ = ["api_router", "register_routes"]
