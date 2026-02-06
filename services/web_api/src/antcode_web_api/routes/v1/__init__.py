from fastapi import APIRouter

from antcode_web_api.routes.v1.alert import router as alert_router
from antcode_web_api.routes.v1.audit import router as audit_router
from antcode_web_api.routes.v1.base import router as base_router
from antcode_web_api.routes.v1.branding import router as branding_router
from antcode_web_api.routes.v1.crawl import router as crawl_router
from antcode_web_api.routes.v1.dashboard import router as dashboard_router
from antcode_web_api.routes.v1.envs import router as envs_router
from antcode_web_api.routes.v1.logs import router as logs_router
from antcode_web_api.routes.v1.monitoring import router as monitoring_router
from antcode_web_api.routes.v1.project import project_router
from antcode_web_api.routes.v1.project_download import router as project_download_router
from antcode_web_api.routes.v1.project_versions import project_versions_router
from antcode_web_api.routes.v1.retry import router as retry_router
from antcode_web_api.routes.v1.runs import runs_router
from antcode_web_api.routes.v1.runtimes import runtime_router
from antcode_web_api.routes.v1.system_config import router as system_config_router
from antcode_web_api.routes.v1.tasks import tasks_router
from antcode_web_api.routes.v1.users import router as users_router
from antcode_web_api.routes.v1.websocket_logs import router as websocket_logs_router
from antcode_web_api.routes.v1.workers import workers_router

v1_router = APIRouter()

v1_router.include_router(base_router, tags=["基础"])
v1_router.include_router(branding_router, prefix="/branding", tags=["基础"])
v1_router.include_router(users_router, prefix="/users", tags=["用户管理"])
v1_router.include_router(project_router, prefix="/projects", tags=["项目管理"])
v1_router.include_router(project_download_router, prefix="/projects", tags=["项目下载"])
v1_router.include_router(project_versions_router, prefix="/projects", tags=["项目版本管理"])
v1_router.include_router(tasks_router, prefix="/tasks", tags=["任务"])
v1_router.include_router(runs_router, prefix="/runs", tags=["任务运行"])
v1_router.include_router(logs_router, prefix="/logs", tags=["日志管理"])
v1_router.include_router(runtime_router, prefix="/runtimes", tags=["运行时管理"])
v1_router.include_router(runtime_router, tags=["运行时管理"])
v1_router.include_router(websocket_logs_router, prefix="/ws", tags=["WebSocket"])
v1_router.include_router(dashboard_router, prefix="/dashboard", tags=["仪表盘"])
v1_router.include_router(envs_router, prefix="/envs", tags=["环境管理"])
v1_router.include_router(monitoring_router, prefix="/monitoring", tags=["监控"])
v1_router.include_router(workers_router, prefix="/workers", tags=["Worker 管理"])
v1_router.include_router(system_config_router, prefix="/system-config", tags=["系统配置"])
v1_router.include_router(alert_router, prefix="/alert", tags=["告警管理"])
v1_router.include_router(audit_router, prefix="/audit", tags=["审计日志"])
v1_router.include_router(retry_router, prefix="/retry", tags=["任务重试"])
v1_router.include_router(crawl_router, prefix="/crawl", tags=["分布式爬取"])

__all__ = ["v1_router"]
