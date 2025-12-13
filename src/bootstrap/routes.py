"""Route registration module.

Provides the register_routes() function for registering all API routes.
"""

from fastapi import FastAPI

from src.api import api_router


def register_routes(app: FastAPI) -> None:
    """Register all API routes to the application.

    Args:
        app: FastAPI application instance.
    """
    app.include_router(api_router, prefix="/api")
