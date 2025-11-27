from fastapi import APIRouter

from src.api.v1.base import router as base_router
from src.api.v1.logs import router as logs_router
from src.api.v1.project import project_router
from src.api.v1.scheduler import router as scheduler_router
from src.api.v1.users import router as users_router
from src.api.v1.websocket_logs import router as websocket_logs_router
from src.api.v1.dashboard import router as dashboard_router
from src.api.v1.envs import router as envs_router
from src.api.v1.monitoring import router as monitoring_router

v1_router = APIRouter()

v1_router.include_router(base_router)
v1_router.include_router(users_router, prefix="/users", tags=["用户管理"])
v1_router.include_router(project_router, prefix="/projects", tags=["项目管理"])
v1_router.include_router(scheduler_router, prefix="/scheduler", tags=["任务调度"])
v1_router.include_router(logs_router, prefix="/logs", tags=["日志管理"])
v1_router.include_router(websocket_logs_router, prefix="/ws", tags=["WebSocket"])
v1_router.include_router(dashboard_router, prefix="/dashboard", tags=["仪表盘"])
v1_router.include_router(envs_router, prefix="/envs", tags=["环境管理"])
v1_router.include_router(monitoring_router, tags=["监控"])

__all__ = ["v1_router"]
