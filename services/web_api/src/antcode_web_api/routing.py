"""Route registration helpers."""

from fastapi import APIRouter


def promote_static_routes(router: APIRouter, paths: set[str]) -> None:
    """Keep selected static routes ahead of catch-all parameter routes."""
    router.routes.sort(key=lambda route: 0 if getattr(route, "path", None) in paths else 1)


__all__ = ["promote_static_routes"]
