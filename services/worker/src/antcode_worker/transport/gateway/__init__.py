"""
Gateway 传输层模块

公网 Worker 通过 Gateway gRPC/TLS 连接。

Requirements: 5.5, 5.6, 5.7
"""

from antcode_worker.transport.gateway.auth import (
    AuthConfig,
    AuthMethod,
    GatewayAuthenticator,
)
from antcode_worker.transport.gateway.codecs import (
    GatewayCodec,
    HeartbeatEncoder,
    LogEncoder,
    ResultEncoder,
    TaskDecoder,
)
from antcode_worker.transport.gateway.reconnect import (
    ReconnectConfig,
    ReconnectManager,
    ReconnectState,
    ReconnectStats,
)
from antcode_worker.transport.gateway.transport import (
    GatewayConfig,
    GatewayTransport,
)

__all__ = [
    # Transport
    "GatewayTransport",
    "GatewayConfig",
    # Auth
    "GatewayAuthenticator",
    "AuthConfig",
    "AuthMethod",
    # Codecs
    "GatewayCodec",
    "TaskDecoder",
    "LogEncoder",
    "ResultEncoder",
    "HeartbeatEncoder",
    # Reconnect
    "ReconnectManager",
    "ReconnectConfig",
    "ReconnectState",
    "ReconnectStats",
]
