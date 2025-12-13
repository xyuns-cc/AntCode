"""Bootstrap module for application initialization.

This module provides:
- Application factory (create_app)
- Lifecycle management (lifespan)
- Route registration (register_routes)
"""

from src.bootstrap.app_factory import create_app
from src.bootstrap.lifespan import lifespan
from src.bootstrap.routes import register_routes

__all__ = ["create_app", "lifespan", "register_routes"]
