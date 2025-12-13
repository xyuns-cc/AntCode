"""
gRPC 服务模块

提供 Master 端 gRPC 服务器实现，用于与 Worker 节点进行双向流通信。
"""

from src.services.grpc.config import GrpcConfig, grpc_config
from src.services.grpc.server import GrpcServer
from src.services.grpc.node_service_impl import NodeServiceImpl
from src.services.grpc.dispatcher import MessageDispatcher, message_dispatcher
from src.services.grpc.handlers import (
    HeartbeatHandler,
    LogHandler,
    TaskStatusHandler,
    TaskDispatcher,
)
from src.services.grpc.performance import (
    PerformanceConfig,
    get_global_performance_config,
    set_global_performance_config,
    get_performance_config,
    apply_performance_profile,
    list_performance_profiles,
)

__all__ = [
    "GrpcConfig",
    "grpc_config",
    "GrpcServer",
    "NodeServiceImpl",
    "MessageDispatcher",
    "message_dispatcher",
    "HeartbeatHandler",
    "LogHandler",
    "TaskStatusHandler",
    "TaskDispatcher",
    # Performance optimization
    "PerformanceConfig",
    "get_global_performance_config",
    "set_global_performance_config",
    "get_performance_config",
    "apply_performance_profile",
    "list_performance_profiles",
]


def register_default_handlers() -> None:
    """注册默认的消息处理器
    
    在 gRPC 服务器启动时调用此函数以注册所有消息处理器。
    """
    from src.services.grpc.handlers.heartbeat_handler import heartbeat_handler
    from src.services.grpc.handlers.log_handler import log_handler
    from src.services.grpc.handlers.task_status_handler import task_status_handler
    from src.services.grpc.handlers.task_dispatcher import task_ack_handler
    
    # 注册心跳处理器
    message_dispatcher.register("heartbeat", heartbeat_handler)
    
    # 注册日志处理器
    message_dispatcher.register("log_batch", log_handler)
    
    # 注册任务状态处理器
    message_dispatcher.register("task_status", task_status_handler)
    
    # 注册任务确认处理器
    message_dispatcher.register("task_ack", task_ack_handler)
