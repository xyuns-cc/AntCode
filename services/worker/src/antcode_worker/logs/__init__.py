"""
日志模块

提供完整的日志管理功能：
- 实时捕获 stdout/stderr
- 本地缓冲（断线恢复）
- 批量发送
- WAL + S3 高可靠归档

Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7
"""

from antcode_worker.logs.streamer import LogStreamer, StreamCapture, LogSink
from antcode_worker.logs.spool import LogSpool, SpoolConfig, SpoolMeta
from antcode_worker.logs.realtime import RealtimeSender, RealtimeConfig, RealtimeSink
from antcode_worker.logs.batch import (
    BatchSender,
    BatchConfig,
    BatchSink,
    BackpressureState,
)
from antcode_worker.logs.archive import (
    LogArchiver,
    ArchiveConfig,
    ArchiveResult,
    ArchiveState,
    ArchiveRecoveryService,
    S3Uploader,
    # 兼容旧接口
    LogArchive,
    SimpleUploader,
)
from antcode_worker.logs.wal import (
    WALWriter,
    WALReader,
    WALManager,
    WALConfig,
    WALEntry,
    WALMetadata,
    WALState,
)
from antcode_worker.logs.manager import LogManager, LogManagerConfig, DropPolicy

__all__ = [
    # Streamer
    "LogStreamer",
    "StreamCapture",
    "LogSink",
    # Spool
    "LogSpool",
    "SpoolConfig",
    "SpoolMeta",
    # Realtime
    "RealtimeSender",
    "RealtimeConfig",
    "RealtimeSink",
    # Batch
    "BatchSender",
    "BatchConfig",
    "BatchSink",
    "BackpressureState",
    # Archive (新)
    "LogArchiver",
    "ArchiveConfig",
    "ArchiveResult",
    "ArchiveState",
    "ArchiveRecoveryService",
    "S3Uploader",
    # Archive (兼容)
    "LogArchive",
    "SimpleUploader",
    # WAL
    "WALWriter",
    "WALReader",
    "WALManager",
    "WALConfig",
    "WALEntry",
    "WALMetadata",
    "WALState",
    # Manager
    "LogManager",
    "LogManagerConfig",
    "DropPolicy",
]
