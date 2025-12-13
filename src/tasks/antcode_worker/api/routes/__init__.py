"""API 路由模块"""

from .node import router as node_router
from .envs import router as envs_router
from .projects import router as projects_router
from .spider import router as spider_router
from .tasks import router as tasks_router
from .queue import router as queue_router

__all__ = [
    "node_router",
    "envs_router",
    "projects_router",
    "tasks_router",
    "spider_router",
    "queue_router",
]
