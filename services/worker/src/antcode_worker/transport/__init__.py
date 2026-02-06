"""
传输层模块

提供 Worker 与 Gateway/Redis 之间的通信抽象。
支持两种模式：
- Direct 模式：内网直连 Redis Streams
- Gateway 模式：公网通过 Gateway gRPC/TLS 连接

Worker 通过 transport.mode 明确选择 Direct（内网直连 Redis）或 Gateway（公网仅连 gRPC 网关）。
两种模式对 Engine 透明，统一遵循 poll→execute→report→ack 语义；
Direct 用 Redis Streams 消费组与 XAUTOCLAIM 保证 at-least-once，
Gateway 由网关代理 Redis/MySQL 并提供 TLS/认证/限流，确保中间件不暴露公网。

Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7
"""

from antcode_worker.transport.base import (
    HeartbeatMessage,
    LogMessage,
    ServerConfig,
    TaskMessage,
    TaskResult,
    TransportBase,
    TransportMode,
    WorkerState,
)

# Transport Factory (配置校验 + 自检 + 创建)
from antcode_worker.transport.factory import (
    DirectConfig,
    GatewayConfigSpec,
    TransportConfig,
    TransportConfigError,
    build_transport_config_from_env,
    create_transport,
    preflight_check_direct,
    preflight_check_gateway,
    print_transport_banner,
    validate_transport_config,
)
from antcode_worker.transport.flow_control import (
    AIMDController,
    BackpressureLevel,
    BackpressureManager,
    FlowControlConfig,
    FlowController,
    FlowControlStats,
    FlowControlStrategy,
    SlidingWindowController,
    TokenBucketController,
    create_flow_controller,
)

# Gateway Transport (模块化实现)
from antcode_worker.transport.gateway import (
    AuthConfig,
    AuthMethod,
    GatewayAuthenticator,
    GatewayCodec,
    GatewayConfig,
    GatewayTransport,
    HeartbeatEncoder,
    LogEncoder,
    ReconnectConfig,
    ReconnectManager,
    ReconnectState,
    ReconnectStats,
    ResultEncoder,
    TaskDecoder,
)

# Redis Transport
from antcode_worker.transport.redis import RedisTransport
from antcode_worker.transport.redis.codecs import (
    CodecError,
    ControlMessageCodec,
    HeartbeatCodec,
    JsonCodec,
    LogMessageCodec,
    ResultMessageCodec,
    SchemaVersion,
    TaskMessageCodec,
    control_codec,
    default_codec,
    heartbeat_codec,
    log_codec,
    result_codec,
    task_codec,
)
from antcode_worker.transport.redis.keys import RedisKeyConfig, RedisKeys, default_keys
from antcode_worker.transport.redis.reclaim import (
    GlobalReclaimer,
    PendingTaskReclaimer,
    ReclaimConfig,
    ReclaimedTask,
    ReclaimStats,
    cleanup_dead_consumers,
    ensure_consumer_group,
)

__all__ = [
    # 基础类
    "TransportBase",
    "ServerConfig",
    "WorkerState",
    "TransportMode",
    # 消息类型
    "TaskMessage",
    "TaskResult",
    "HeartbeatMessage",
    "LogMessage",
    # 流量控制
    "FlowController",
    "FlowControlConfig",
    "FlowControlStats",
    "FlowControlStrategy",
    "BackpressureLevel",
    "BackpressureManager",
    "TokenBucketController",
    "AIMDController",
    "SlidingWindowController",
    "create_flow_controller",
    # 具体实现
    "RedisTransport",
    "GatewayTransport",
    # Gateway 配置
    "GatewayConfig",
    # Gateway 认证
    "GatewayAuthenticator",
    "AuthConfig",
    "AuthMethod",
    # Gateway 编解码
    "GatewayCodec",
    "TaskDecoder",
    "LogEncoder",
    "ResultEncoder",
    "HeartbeatEncoder",
    # Gateway 重连
    "ReconnectManager",
    "ReconnectConfig",
    "ReconnectState",
    "ReconnectStats",
    # Redis Keys
    "RedisKeys",
    "RedisKeyConfig",
    "default_keys",
    # Redis Codecs
    "JsonCodec",
    "TaskMessageCodec",
    "LogMessageCodec",
    "ResultMessageCodec",
    "HeartbeatCodec",
    "ControlMessageCodec",
    "SchemaVersion",
    "CodecError",
    "default_codec",
    "task_codec",
    "log_codec",
    "result_codec",
    "heartbeat_codec",
    "control_codec",
    # Redis Reclaim
    "PendingTaskReclaimer",
    "GlobalReclaimer",
    "ReclaimedTask",
    "ReclaimConfig",
    "ReclaimStats",
    "ensure_consumer_group",
    "cleanup_dead_consumers",
    # Transport Factory
    "TransportConfig",
    "TransportConfigError",
    "DirectConfig",
    "GatewayConfigSpec",
    "validate_transport_config",
    "print_transport_banner",
    "preflight_check_direct",
    "preflight_check_gateway",
    "create_transport",
    "build_transport_config_from_env",
]
