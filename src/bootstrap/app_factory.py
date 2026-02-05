"""Application factory module.

Provides the create_app() factory function for creating FastAPI application instances.
"""

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError

from src.bootstrap.lifespan import lifespan
from src.bootstrap.routes import register_routes
from src.core.config import settings
from src.core.exceptions import (
    BusinessException,
    business_exception_handler,
    http_exception_handler,
    validation_exception_handler,
    general_exception_handler,
)
from src.core.logging import setup_logging
from src.infrastructure.middleware import make_middlewares


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns:
        FastAPI: Configured application instance.
    """
    setup_logging()

    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description=settings.APP_DESCRIPTION,
        middleware=make_middlewares(),
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )

    # Register exception handlers
    app.add_exception_handler(BusinessException, business_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, general_exception_handler)

    # Register routes
    register_routes(app)

    return app
