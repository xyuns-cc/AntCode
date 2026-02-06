"""
日志存储模块

提供可插拔的日志持久化存储：
- base: 日志存储后端抽象接口
- s3: S3/MinIO 日志存储后端
- clickhouse: ClickHouse 日志存储后端（预留）

使用方式：
    from antcode_core.infrastructure.storage.log_storage import get_log_storage
    
    storage = get_log_storage()
    await storage.write_log(run_id, log_type, content, seq)
    await storage.write_chunk(run_id, log_type, chunk, offset)
"""

from antcode_core.infrastructure.storage.log_storage.base import (
    LogStorageBackend,
    LogEntry,
    LogChunk,
    WriteResult,
    get_log_storage,
    reset_log_storage,
)

__all__ = [
    "LogStorageBackend",
    "LogEntry",
    "LogChunk",
    "WriteResult",
    "get_log_storage",
    "reset_log_storage",
]
