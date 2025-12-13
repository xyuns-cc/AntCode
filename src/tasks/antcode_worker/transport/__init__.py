"""
传输层 - Worker 节点通信协议实现

本模块包含所有与 Master 通信的传输协议实现。
支持 gRPC、HTTP 和 WebSocket 协议。

Requirements: 11.2
"""

from .protocol import TransportProtocol, TransportError, ConnectionError, SendError
from .http_client import HttpClient
from .grpc_client import GrpcClient
from .resilient_client import (
    ResilientGrpcClient,
    ExponentialBackoff,
    MessageBuffer,
    BufferedMessage,
    MessageType,
)
from .communication_manager import CommunicationManager

__all__ = [
    "TransportProtocol",
    "TransportError",
    "ConnectionError",
    "SendError",
    "HttpClient",
    "GrpcClient",
    "ResilientGrpcClient",
    "ExponentialBackoff",
    "MessageBuffer",
    "BufferedMessage",
    "MessageType",
    "CommunicationManager",
]
