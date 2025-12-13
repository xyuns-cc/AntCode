"""Application entry point.

This module provides the FastAPI application instance created by the bootstrap module.

The application is created lazily to avoid side effects during module imports.
Use `get_app()` to get the application instance, or access `app` directly
which will create the application on first access.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

_app: "FastAPI | None" = None


def get_app() -> "FastAPI":
    """Get or create the FastAPI application instance.
    
    Returns:
        FastAPI: The application instance.
    """
    global _app
    if _app is None:
        from src.bootstrap import create_app
        _app = create_app()
    return _app


# For backward compatibility with `from src import app`
def __getattr__(name: str):
    if name == "app":
        return get_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
