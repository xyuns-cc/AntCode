"""
业务服务层

- env_service: 虚拟环境管理
- project_service: 项目文件管理
- master_client: 主节点 HTTP 通信
- websocket_client: WebSocket 实时通信 (已弃用，请使用 transport.CommunicationManager)
- capability_service: 节点能力检测
- communication_manager: 统一通讯管理（WebSocket 优先 + HTTP 回退）(已弃用，请使用 transport.CommunicationManager)
- log_buffer: 日志缓冲器（批量上报、压缩、重试）
- project_cache: 项目缓存（基于 file_hash 的 LRU 缓存）

.. deprecated:: 2.0.0
    以下模块已弃用，请使用 transport 层替代:
    - websocket_client -> transport.CommunicationManager + transport.grpc_client
    - communication_manager (services) -> transport.CommunicationManager
    
    迁移指南请参考: docs/grpc-communication.md
"""

from .env_service import LocalEnvService, local_env_service
from .project_service import LocalProjectService, local_project_service
from .master_client import MasterClient, master_client
from .websocket_client import NodeWebSocketClient, node_ws_client
from .capability_service import CapabilityService, capability_service
from .communication_manager import CommunicationManager, communication_manager
from .log_buffer import LogBuffer, LogBufferEntry, LogBufferStats, compress_logs, decompress_logs
from .project_cache import ProjectCache, ProjectCacheEntry

__all__ = [
    "LocalEnvService",
    "local_env_service",
    "LocalProjectService",
    "local_project_service",
    "MasterClient",
    "master_client",
    "NodeWebSocketClient",
    "node_ws_client",
    "CapabilityService",
    "capability_service",
    "CommunicationManager",
    "communication_manager",
    "LogBuffer",
    "LogBufferEntry",
    "LogBufferStats",
    "compress_logs",
    "decompress_logs",
    "ProjectCache",
    "ProjectCacheEntry",
]
