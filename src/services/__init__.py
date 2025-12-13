"""业务服务"""
from src.services.files import FileStorageService
from src.services.logs import LogSecurityService, TaskLogService
from src.services.projects import ProjectService, ProjectFileService, UnifiedProjectService, RelationService
from src.services.scheduler import SchedulerService, TaskExecutor
from src.services.users import UserService
from src.services.websockets import WebSocketConnectionManager, WebSocketLogService
from src.services.grpc import GrpcServer, GrpcConfig, grpc_config, NodeServiceImpl, MessageDispatcher

log_security_service = LogSecurityService()
task_log_service = TaskLogService()

websocket_connection_manager = WebSocketConnectionManager()
websocket_log_service = WebSocketLogService()

project_service = ProjectService()
project_file_service = ProjectFileService()
unified_project_service = UnifiedProjectService()
relation_service = RelationService()

scheduler_service = SchedulerService()
task_executor = TaskExecutor()

user_service = UserService()

file_storage = FileStorageService()

__all__ = [
    "log_security_service",
    "task_log_service",
    "websocket_connection_manager",
    "websocket_log_service",
    "project_service",
    "project_file_service",
    "unified_project_service",
    "relation_service",
    "scheduler_service",
    "task_executor",
    "user_service",
    "file_storage",
    "LogSecurityService",
    "TaskLogService",
    "WebSocketConnectionManager",
    "WebSocketLogService",
    "ProjectService",
    "ProjectFileService",
    "UnifiedProjectService",
    "RelationService",
    "SchedulerService",
    "TaskExecutor",
    "UserService",
    "FileStorageService",
    # gRPC 服务
    "GrpcServer",
    "GrpcConfig",
    "grpc_config",
    "NodeServiceImpl",
    "MessageDispatcher",
]
