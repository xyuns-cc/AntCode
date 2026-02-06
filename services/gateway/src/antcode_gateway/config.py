"""
Gateway 服务配置模块

从环境变量读取 gRPC 相关配置。
"""

import os
from dataclasses import dataclass, field


@dataclass
class GatewayConfig:
    """Gateway 服务器配置

    配置项从环境变量读取，支持通过 .env 文件配置。
    """

    # 服务器地址
    host: str = field(default_factory=lambda: os.getenv("GRPC_HOST", "0.0.0.0"))

    # 服务器端口
    port: int = field(default_factory=lambda: int(os.getenv("GRPC_PORT", "50051")))

    # 最大工作线程数
    max_workers: int = field(
        default_factory=lambda: int(os.getenv("GRPC_MAX_WORKERS", "10"))
    )

    # 是否启用 gRPC 服务
    enabled: bool = field(
        default_factory=lambda: os.getenv("GRPC_ENABLED", "true").lower() == "true"
    )

    # 最大发送消息大小 (50MB)
    max_send_message_length: int = 50 * 1024 * 1024

    # 最大接收消息大小 (50MB)
    max_receive_message_length: int = 50 * 1024 * 1024

    # 心跳保活时间（毫秒）
    keepalive_time_ms: int = 30000

    # 心跳保活超时（毫秒）
    keepalive_timeout_ms: int = 10000

    # 允许无调用时发送 keepalive ping
    keepalive_permit_without_calls: bool = True

    # 心跳间隔 (秒)
    heartbeat_interval: int = field(
        default_factory=lambda: int(os.getenv("GRPC_HEARTBEAT_INTERVAL", "30"))
    )

    # 心跳超时 (秒) - 超过此时间未收到心跳则标记节点离线
    heartbeat_timeout: int = field(
        default_factory=lambda: int(os.getenv("GRPC_HEARTBEAT_TIMEOUT", "90"))
    )

    # 优雅关闭等待时间 (秒)
    shutdown_grace_period: float = field(
        default_factory=lambda: float(os.getenv("GRPC_SHUTDOWN_GRACE_PERIOD", "5.0"))
    )

    # TLS 配置 (可选)
    tls_cert_path: str | None = field(
        default_factory=lambda: os.getenv("GRPC_TLS_CERT_PATH") or None
    )
    tls_key_path: str | None = field(
        default_factory=lambda: os.getenv("GRPC_TLS_KEY_PATH") or None
    )
    tls_ca_path: str | None = field(
        default_factory=lambda: os.getenv("GRPC_TLS_CA_PATH") or None
    )

    # 认证配置
    auth_enabled: bool = field(
        default_factory=lambda: os.getenv("AUTH_ENABLED", "true").lower() == "true"
    )

    # 限流配置
    rate_limit_enabled: bool = field(
        default_factory=lambda: os.getenv("RATE_LIMIT_ENABLED", "true").lower() == "true"
    )
    rate_limit_rate: int = field(
        default_factory=lambda: int(os.getenv("RATE_LIMIT_RATE", "100"))
    )
    rate_limit_capacity: int = field(
        default_factory=lambda: int(os.getenv("RATE_LIMIT_CAPACITY", "200"))
    )

    # Redis 配置（用于 Streams 读取）
    redis_url: str = field(
        default_factory=lambda: os.getenv("REDIS_URL", "redis://localhost:6379/0")
    )

    @property
    def server_options(self) -> list[tuple]:
        """获取 gRPC 服务器选项"""
        return [
            ("grpc.max_send_message_length", self.max_send_message_length),
            ("grpc.max_receive_message_length", self.max_receive_message_length),
            ("grpc.keepalive_time_ms", self.keepalive_time_ms),
            ("grpc.keepalive_timeout_ms", self.keepalive_timeout_ms),
            (
                "grpc.keepalive_permit_without_calls",
                self.keepalive_permit_without_calls,
            ),
            ("grpc.http2.min_recv_ping_interval_without_data_ms", 10000),
            ("grpc.http2.max_pings_without_data", 0),
        ]

    @property
    def tls_enabled(self) -> bool:
        """是否启用 TLS"""
        return bool(self.tls_cert_path and self.tls_key_path)

    @property
    def mtls_enabled(self) -> bool:
        """是否启用 mTLS（双向 TLS）"""
        return self.tls_enabled and bool(self.tls_ca_path)

    @property
    def listen_address(self) -> str:
        """获取监听地址"""
        return f"{self.host}:{self.port}"


# 全局配置实例
gateway_config = GatewayConfig()
