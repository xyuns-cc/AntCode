"""
Auto-generated gRPC code from Protocol Buffers.
Do not edit manually - regenerate using: python scripts/generate_proto.py
"""

from .common_pb2 import Timestamp, Metrics, OSInfo
from .node_service_pb2 import (
    NodeMessage,
    MasterMessage,
    Heartbeat,
    LogBatch,
    LogEntry,
    TaskStatus,
    TaskDispatch,
    TaskAck,
    TaskCancel,
    CancelAck,
    RegisterRequest,
    RegisterResponse,
    ConfigUpdate,
    Ping,
)
from .node_service_pb2_grpc import (
    NodeServiceServicer,
    NodeServiceStub,
    add_NodeServiceServicer_to_server,
)

__all__ = [
    # Common types
    "Timestamp",
    "Metrics",
    "OSInfo",
    # Node messages
    "NodeMessage",
    "MasterMessage",
    "Heartbeat",
    "LogBatch",
    "LogEntry",
    "TaskStatus",
    "TaskDispatch",
    "TaskAck",
    "TaskCancel",
    "CancelAck",
    "RegisterRequest",
    "RegisterResponse",
    "ConfigUpdate",
    "Ping",
    # gRPC service
    "NodeServiceServicer",
    "NodeServiceStub",
    "add_NodeServiceServicer_to_server",
]
