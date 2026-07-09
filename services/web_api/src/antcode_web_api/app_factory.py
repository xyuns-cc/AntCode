"""应用工厂模块。

提供 create_app() 工厂函数，用于创建 FastAPI 应用实例。
"""

from antcode_core.common.config import settings
from antcode_core.common.logging import setup_logging
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import UJSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from antcode_web_api.exceptions import (
    BusinessException,
    business_exception_handler,
    general_exception_handler,
    http_exception_handler,
    validation_exception_handler,
)
from antcode_web_api.lifespan import lifespan
from antcode_web_api.middleware import make_middlewares
from antcode_web_api.openapi import (
    API_TAGS,
    DEFAULT_ERROR_RESPONSES,
    generate_operation_id,
)


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用。

    Returns:
        FastAPI: 已配置的应用实例。
    """
    setup_logging()
    # 进程级角色锁定：web_api 进程固定为控制面，禁止与调度器（master）合并部署
    settings.SCHEDULER_ROLE = "control"
    from antcode_web_api.routes import register_routes

    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description=settings.APP_DESCRIPTION,
        middleware=make_middlewares(),
        default_response_class=UJSONResponse,
        openapi_tags=API_TAGS,
        responses=DEFAULT_ERROR_RESPONSES,
        generate_unique_id_function=generate_operation_id,
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # 注册异常处理器
    app.add_exception_handler(BusinessException, business_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, general_exception_handler)

    # 注册路由
    register_routes(app)

    # T7-P2-3: /metrics 端点（Prometheus 惯例挂根路径，与 API v1 分离）
    try:
        from antcode_web_api.prometheus_metrics import router as prom_router

        app.include_router(prom_router)
    except ImportError:
        pass

    return app
