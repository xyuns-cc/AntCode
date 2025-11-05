# src/__init__.py
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi import HTTPException
from loguru import logger
from tortoise import Tortoise

from src.api import api_router
from src.core.config import settings
from src.core.exceptions import (
    BusinessException, business_exception_handler,
    http_exception_handler, validation_exception_handler,
    general_exception_handler
)
from src.core.init_app import init_data, make_middlewares, shutdown_scheduler


@asynccontextmanager
async def lifespan(app):
    try:
        await init_data()
        logger.info("✅ 应用启动完成")
        yield
    except Exception as e:
        logger.error(f"❌ 应用启动失败: {e}")
        raise
    finally:
        logger.info("🔄 正在关闭调度器...")
        await shutdown_scheduler()
        logger.info("🔄 正在关闭数据库连接...")
        await Tortoise.close_connections()
        logger.info("👋 应用已停止")


def create_app():
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description=settings.APP_DESCRIPTION,
        middleware=make_middlewares(),
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # 添加异常处理器
    app.add_exception_handler(BusinessException, business_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, general_exception_handler)

    app.include_router(api_router, prefix='/api')
    return app


# 创建应用实例
app = create_app()
