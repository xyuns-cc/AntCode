"""
业务服务层

- env_service: 虚拟环境管理
- project_service: 项目文件管理
- master_client: 主节点 HTTP 通信
- capability_service: 节点能力检测
- log_buffer: 日志缓冲器（批量上报、压缩、重试）
- project_cache: 项目缓存（基于 file_hash 的 LRU 缓存）
"""

from .env_service import LocalEnvService, local_env_service
from .project_service import LocalProjectService, local_project_service
from .master_client import MasterClient, master_client
from .capability_service import CapabilityService, capability_service
from .log_buffer import LogBuffer, LogBufferEntry, LogBufferStats, compress_logs, decompress_logs
from .project_cache import ProjectCache, ProjectCacheEntry

__all__ = [
    "LocalEnvService",
    "local_env_service",
    "LocalProjectService",
    "local_project_service",
    "MasterClient",
    "master_client",
    "CapabilityService",
    "capability_service",
    "LogBuffer",
    "LogBufferEntry",
    "LogBufferStats",
    "compress_logs",
    "decompress_logs",
    "ProjectCache",
    "ProjectCacheEntry",
]
