"""
日志模块

提供完整的日志管理功能：
- 实时捕获 stdout/stderr
- 批量发送
- transport 上报

Requirements: 9.1, 9.2, 9.4, 9.5, 9.7
"""

from antcode_worker.logs.batch import (
    BackpressureState,
    BatchConfig,
    BatchSender,
    BatchSink,
)
from antcode_worker.logs.manager import DropPolicy, LogManager, LogManagerConfig
from antcode_worker.logs.realtime import RealtimeConfig, RealtimeSender, RealtimeSink
from antcode_worker.logs.streamer import LogSink, LogStreamer, StreamCapture

__all__ = [
    # Streamer
    "LogStreamer",
    "StreamCapture",
    "LogSink",
    # Realtime
    "RealtimeSender",
    "RealtimeConfig",
    "RealtimeSink",
    # Batch
    "BatchSender",
    "BatchConfig",
    "BatchSink",
    "BackpressureState",
    # Manager
    "LogManager",
    "LogManagerConfig",
    "DropPolicy",
]
