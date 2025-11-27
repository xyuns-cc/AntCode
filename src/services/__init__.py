"""业务服务"""
from src.services.files import AsyncFileStreamService, FileStorageService
from src.services.logs import LogSecurityService, LogPerformanceMonitor, TaskLogService
from src.services.projects import ProjectService, ProjectFileService, UnifiedProjectService, RelationService
from src.services.scheduler import SchedulerService, TaskExecutor, RedisTaskService
from src.services.users import UserService
from src.services.websockets import WebSocketConnectionManager, WebSocketLogService

log_security_service = LogSecurityService()
log_performance_service = LogPerformanceMonitor()
task_log_service = TaskLogService()

websocket_connection_manager = WebSocketConnectionManager()
websocket_log_service = WebSocketLogService()

project_service = ProjectService()
project_file_service = ProjectFileService()
unified_project_service = UnifiedProjectService()
relation_service = RelationService()

scheduler_service = SchedulerService()
task_executor = TaskExecutor()
redis_task_service = RedisTaskService()

user_service = UserService()

async_file_stream_service = AsyncFileStreamService()
file_storage = FileStorageService()

__all__ = [
    "log_security_service",
    "log_performance_service", 
    "task_log_service",
    "websocket_connection_manager",
    "websocket_log_service",
    "project_service",
    "project_file_service",
    "unified_project_service",
    "relation_service",
    "scheduler_service",
    "task_executor",
    "redis_task_service",
    "user_service",
    "async_file_stream_service",
    "file_storage",
    "LogSecurityService",
    "LogPerformanceMonitor",
    "TaskLogService",
    "WebSocketConnectionManager",
    "WebSocketLogService",
    "ProjectService",
    "ProjectFileService",
    "UnifiedProjectService",
    "RelationService",
    "SchedulerService",
    "TaskExecutor",
    "RedisTaskService",
    "UserService",
    "AsyncFileStreamService",
    "FileStorageService"
]
