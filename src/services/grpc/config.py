"""
gRPC 服务配置模块

从主配置 (src.core.config.settings) 读取 gRPC 相关配置。
"""
from dataclasses import dataclass, field
from typing import Optional


def _get_settings():
    """延迟导入 settings 以避免循环导入"""
    from src.core.config import settings
    return settings


@dataclass
class GrpcConfig:
    """gRPC 服务器配置
    
    配置项从 src.core.config.settings 读取，支持通过 .env 文件配置。
    """
    
    # 服务器端口
    port: int = field(default_factory=lambda: _get_settings().GRPC_PORT)
    
    # 最大工作线程数
    max_workers: int = field(default_factory=lambda: _get_settings().GRPC_MAX_WORKERS)
    
    # 是否启用 gRPC 服务
    enabled: bool = field(default_factory=lambda: _get_settings().GRPC_ENABLED)
    
    # 最大发送消息大小 (50MB)
    max_send_message_length: int = 50 * 1024 * 1024
    
    # 最大接收消息大小 (50MB)
    max_receive_message_length: int = 50 * 1024 * 1024
    
    # Keepalive 时间 (毫秒)
    keepalive_time_ms: int = 30000
    
    # Keepalive 超时 (毫秒)
    keepalive_timeout_ms: int = 10000
    
    # 允许无调用时发送 keepalive ping
    keepalive_permit_without_calls: bool = True
    
    # 心跳间隔 (秒)
    heartbeat_interval: int = field(default_factory=lambda: _get_settings().GRPC_HEARTBEAT_INTERVAL)
    
    # 心跳超时 (秒) - 超过此时间未收到心跳则标记节点离线
    heartbeat_timeout: int = field(default_factory=lambda: _get_settings().GRPC_HEARTBEAT_TIMEOUT)
    
    # 优雅关闭等待时间 (秒)
    shutdown_grace_period: float = field(default_factory=lambda: _get_settings().GRPC_SHUTDOWN_GRACE_PERIOD)
    
    # TLS 配置 (可选)
    tls_cert_path: Optional[str] = field(default_factory=lambda: _get_settings().GRPC_TLS_CERT_PATH or None)
    tls_key_path: Optional[str] = field(default_factory=lambda: _get_settings().GRPC_TLS_KEY_PATH or None)
    
    @property
    def server_options(self) -> list[tuple]:
        """获取 gRPC 服务器选项"""
        return [
            ("grpc.max_send_message_length", self.max_send_message_length),
            ("grpc.max_receive_message_length", self.max_receive_message_length),
            ("grpc.keepalive_time_ms", self.keepalive_time_ms),
            ("grpc.keepalive_timeout_ms", self.keepalive_timeout_ms),
            ("grpc.keepalive_permit_without_calls", self.keepalive_permit_without_calls),
            ("grpc.http2.min_recv_ping_interval_without_data_ms", 10000),
            ("grpc.http2.max_pings_without_data", 0),
        ]
    
    @property
    def tls_enabled(self) -> bool:
        """是否启用 TLS"""
        return bool(self.tls_cert_path and self.tls_key_path)


# 全局配置实例
grpc_config = GrpcConfig()
