"""
服务模块
包含所有业务逻辑服务
"""
from .files import (
    AsyncFileStreamService,
    FileStorageService
)
# 从各个子模块导入服务
from .logs import (
    LogSecurityService,
    LogPerformanceMonitor,
    TaskLogService
)
from .projects import (
    ProjectService,
    ProjectFileService,
    UnifiedProjectService,
    RelationService
)
from .scheduler import (
    SchedulerService,
    TaskExecutor,
    RedisTaskService
)
from .users import UserService
from .websockets import (
    WebSocketConnectionManager,
    WebSocketLogService
)

# 创建服务实例
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
    # 日志服务
    "log_security_service",
    "log_performance_service", 
    "task_log_service",
    
    # WebSocket服务
    "websocket_connection_manager",
    "websocket_log_service",
    
    # 项目管理服务
    "project_service",
    "project_file_service",
    "unified_project_service",
    "relation_service",
    
    # 调度器服务
    "scheduler_service",
    "task_executor",
    "redis_task_service",
    
    # 用户管理服务
    "user_service",
    
    # 文件处理服务
    "async_file_stream_service",
    "file_storage",
    
    # 服务类
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