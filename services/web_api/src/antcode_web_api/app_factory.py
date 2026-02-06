"""应用工厂模块。

提供 create_app() 工厂函数，用于创建 FastAPI 应用实例。
"""

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError

from antcode_core.common.config import settings
from antcode_core.common.logging import setup_logging
from antcode_web_api.exceptions import (
    BusinessException,
    business_exception_handler,
    general_exception_handler,
    http_exception_handler,
    validation_exception_handler,
)
from antcode_web_api.lifespan import lifespan
from antcode_web_api.middleware import make_middlewares


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用。

    Returns:
        FastAPI: 已配置的应用实例。
    """
    setup_logging()
    settings.SCHEDULER_ROLE = "control"
    from antcode_web_api.routes import register_routes

    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description=settings.APP_DESCRIPTION,
        middleware=make_middlewares(),
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # 注册异常处理器
    app.add_exception_handler(BusinessException, business_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, general_exception_handler)

    # 注册路由
    register_routes(app)

    return app
